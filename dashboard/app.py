"""
Trading Dashboard — Flask Web App
Opens on http://localhost:8080
Auto-refreshes every 30 seconds.

Run: python3 dashboard/app.py
"""

import sys
import os
import csv
import json
from datetime import datetime, date, timedelta, time as dt_time
from flask import Flask, render_template, jsonify

# Add parent dir to path so we can import config
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env"))

from config.settings import (
    RISK_CONFIG, MARKET_OPEN_TIME, MARKET_CLOSE_TIME,
    PAPER_TRADING, ENABLED_STRATEGIES,
    TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
)

app = Flask(__name__)

BASE_DIR        = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TRADES_LOG      = os.path.join(BASE_DIR, "logs", "trades.csv")
SIGNALS_LOG     = os.path.join(BASE_DIR, "logs", "signals.csv")
BT_SUMMARY_CSV  = os.path.join(BASE_DIR, "backtest_results", "backtest_summary.csv")
BT_TRADES_CSV   = os.path.join(BASE_DIR, "backtest_results", "backtest_trades.csv")
BT_EQUITY_JSON  = os.path.join(BASE_DIR, "backtest_results", "equity_curves.json")
IV_CACHE_PATH   = os.path.join(BASE_DIR, "logs", "iv_cache.json")
CPR_CACHE_PATH  = os.path.join(BASE_DIR, "logs", "cpr_cache.json")

# ─────────────────────────────────────────────
# ACTIVE FUTURES CONTRACTS  ← UPDATE MONTHLY on expiry rollover
# ─────────────────────────────────────────────
# Segment: NSE_FNO  |  Instrument: FUTIDX
# These are used for: LTP, CPR candles, and all dashboard price displays.
# How to find new IDs after rollover: /api/debug/full  →  look for new contract in Dhan instrument list.
#
# April 2026 expiry:
#   NIFTY  Apr 2026 FUT  security_id = 62329   expires 2026-04-24
#   BANKNIFTY Apr 2026 FUT security_id = 62326  expires 2026-04-29
#
# ⚠️  UPDATE security_id here when front month rolls over (usually last Thursday of each month)

INSTRUMENT_IDS = {
    "NIFTY": {
        "security_id": "62329",
        "exchange":    "NSE_FNO",
        "instrument":  "FUTIDX",
        "label":       "NIFTY Jun FUT",
        "expiry":      "2026-06-30",
    },
    "BANKNIFTY": {
        "security_id": "62326",
        "exchange":    "NSE_FNO",
        "instrument":  "FUTIDX",
        "label":       "BANKNIFTY Jun FUT",
        "expiry":      "2026-06-30",
    },
}

# Alias — CPR helper uses FUTURES_IDS; keep it in sync with INSTRUMENT_IDS above
FUTURES_IDS = INSTRUMENT_IDS

# NSE IDX_I IDs — used ONLY for the Dhan OC API (UnderlyingScrip must be index ID, not futures)
# These are stable and never change.
_INDEX_IDS_FOR_OC = {
    "NIFTY":     "13",
    "BANKNIFTY": "25",
}

# ─────────────────────────────────────────────
# LAZY SINGLETONS
# ─────────────────────────────────────────────

_news_analyzer = None
_dhan_client   = None


def _get_news_analyzer():
    global _news_analyzer
    if _news_analyzer is None:
        from data.news_sentiment import NewsSentimentAnalyzer
        _news_analyzer = NewsSentimentAnalyzer()
    return _news_analyzer


def _get_dhan_client():
    global _dhan_client
    if _dhan_client is None:
        try:
            from data.dhan_client import DhanClient
            _dhan_client = DhanClient()
        except Exception as e:
            return None
    return _dhan_client


# ─────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────

def is_market_open():
    now = datetime.now().time()
    oh, om = map(int, MARKET_OPEN_TIME.split(":"))
    ch, cm = map(int, MARKET_CLOSE_TIME.split(":"))
    return dt_time(oh, om) <= now <= dt_time(ch, cm) and datetime.now().weekday() < 5


def read_trades():
    if not os.path.exists(TRADES_LOG):
        return []
    trades = []
    with open(TRADES_LOG, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            trades.append(row)
    return list(reversed(trades[-20:]))


def read_signals():
    if not os.path.exists(SIGNALS_LOG):
        return []
    signals = []
    with open(SIGNALS_LOG, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            signals.append(row)
    return list(reversed(signals[-10:]))


def compute_summary(trades):
    today = datetime.now().strftime("%Y-%m-%d")
    today_trades = [t for t in trades if today in t.get("timestamp", "")]
    closed = [t for t in today_trades if t.get("event") == "CLOSE"]
    open_t = [t for t in today_trades if t.get("status") == "OPEN"]

    pnl = sum(float(t.get("pnl", 0)) for t in closed)
    winners = [t for t in closed if float(t.get("pnl", 0)) > 0]
    losers  = [t for t in closed if float(t.get("pnl", 0)) <= 0]

    total_capital = 500_000.0
    available     = total_capital + pnl

    daily_loss = abs(min(pnl, 0))
    loss_pct   = daily_loss / total_capital * 100
    max_loss   = RISK_CONFIG.max_daily_loss_pct

    return {
        "pnl":               round(pnl, 2),
        "pnl_pct":           round(pnl / total_capital * 100, 2),
        "open_positions":    len(open_t),
        "total_trades":      len(today_trades),
        "closed_trades":     len(closed),
        "winners":           len(winners),
        "losers":            len(losers),
        "win_rate":          (len(winners) / len(closed) * 100) if closed else 0,
        "available_capital": available,
        "total_capital":     total_capital,
        "loss_used_pct":     min(loss_pct / max_loss * 100, 100),
        "daily_loss_breached": loss_pct >= max_loss,
        "max_positions":     RISK_CONFIG.max_open_positions,
        "max_trades":        RISK_CONFIG.max_intraday_trades,
        "max_daily_loss_pct": max_loss,
    }


def get_open_positions(trades):
    open_ids = {}
    for t in reversed(trades):
        tid = t.get("id", "")
        if t.get("event") == "OPEN" and tid not in open_ids:
            open_ids[tid] = t
        elif t.get("event") == "CLOSE" and tid in open_ids:
            del open_ids[tid]

    positions = []
    for t in open_ids.values():
        class Pos:
            pass
        p = Pos()
        p.symbol          = t.get("symbol", "")
        p.instrument_type = t.get("instrument_type", "EQ")
        p.direction       = t.get("direction", "BUY")
        p.entry_price     = float(t.get("entry_price", 0))
        p.current_price   = float(t.get("entry_price", 0))
        p.stop_loss       = float(t.get("stop_loss", 0))
        p.unrealised_pnl  = 0.0
        positions.append(p)
    return positions


def format_signals(raw_signals):
    result = []
    for s in raw_signals:
        result.append({
            "time":            s.get("timestamp", "")[-8:][:5],
            "symbol":          s.get("symbol", ""),
            "signal":          s.get("signal", "WAIT"),
            "instrument_type": s.get("instrument_type", ""),
            "confidence":      float(s.get("confidence", 0)),
            "rr":              float(s.get("risk_reward", 0)),
        })
    return result


def format_trades(raw_trades):
    result = []
    for t in raw_trades:
        if t.get("event") != "CLOSE":
            continue
        result.append({
            "time":            t.get("timestamp", "")[-8:][:5],
            "symbol":          t.get("symbol", ""),
            "instrument_type": t.get("instrument_type", "EQ"),
            "direction":       t.get("direction", "BUY"),
            "entry_price":     t.get("entry_price", ""),
            "exit_price":      t.get("exit_price", ""),
            "quantity":        t.get("quantity", ""),
            "pnl":             float(t.get("pnl", 0)),
            "status":          t.get("status", ""),
        })
    return result


# ─────────────────────────────────────────────
# ROUTES
# ─────────────────────────────────────────────

@app.route("/")
def index():
    raw_trades  = read_trades()
    raw_signals = read_signals()
    summary     = compute_summary(raw_trades)
    positions   = get_open_positions(raw_trades)
    signals     = format_signals(raw_signals)
    trades      = format_trades(raw_trades)

    active_strategies = sum(1 for v in ENABLED_STRATEGIES.values() if v)
    telegram_ok       = bool(TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID)

    return render_template(
        "index.html",
        now               = datetime.now().strftime("%d %b %Y, %H:%M:%S"),
        paper_mode        = PAPER_TRADING,
        market_open       = is_market_open(),
        positions         = positions,
        signals           = signals,
        trades            = trades,
        active_strategies = active_strategies,
        telegram_ok       = telegram_ok,
        **summary,
    )


@app.route("/api/summary")
def api_summary():
    raw_trades = read_trades()
    summary    = compute_summary(raw_trades)
    summary["market_open"] = is_market_open()
    summary["timestamp"]   = str(datetime.now())
    return json.dumps(summary)


@app.route("/api/trades")
def api_trades():
    return json.dumps(read_trades()[-50:])


@app.route("/api/signals")
def api_signals():
    return json.dumps(read_signals()[-20:])


# ─────────────────────────────────────────────
# BACKTEST HELPERS
# ─────────────────────────────────────────────

def read_bt_summary():
    if not os.path.exists(BT_SUMMARY_CSV):
        return []
    rows = []
    with open(BT_SUMMARY_CSV, newline="") as f:
        for row in csv.DictReader(f):
            rows.append(row)
    return rows

def read_bt_trades():
    if not os.path.exists(BT_TRADES_CSV):
        return []
    rows = []
    with open(BT_TRADES_CSV, newline="") as f:
        for row in csv.DictReader(f):
            rows.append(row)
    return rows

def read_bt_equity():
    if not os.path.exists(BT_EQUITY_JSON):
        return {}
    with open(BT_EQUITY_JSON) as f:
        return json.load(f)

def bt_monthly_pnl(trades):
    monthly = {}
    for t in trades:
        month  = t.get("entry_date", "")[:7]
        symbol = t.get("symbol", "")
        pnl    = float(t.get("pnl_net", 0))
        if month not in monthly:
            monthly[month] = {}
        monthly[month][symbol] = monthly[month].get(symbol, 0) + pnl
    return dict(sorted(monthly.items()))


# ─────────────────────────────────────────────
# BACKTEST ROUTE
# ─────────────────────────────────────────────

@app.route("/backtest")
def backtest():
    summary   = read_bt_summary()
    trades    = read_bt_trades()
    equity    = read_bt_equity()
    monthly   = bt_monthly_pnl(trades)
    has_data  = bool(summary)

    total_trades = sum(int(r.get("total_trades", 0)) for r in summary)
    total_pnl    = sum(float(r.get("total_pnl", 0)) for r in summary)
    total_wins   = sum(int(r.get("winners", 0)) for r in summary)
    overall_wr   = total_wins / total_trades * 100 if total_trades > 0 else 0
    initial_cap  = 500_000.0
    final_cap    = initial_cap + total_pnl
    ret_pct      = total_pnl / initial_cap * 100

    sorted_trades = sorted(trades, key=lambda t: float(t.get("pnl_net", 0)), reverse=True)
    best_trade   = sorted_trades[0]  if sorted_trades else None
    worst_trade  = sorted_trades[-1] if sorted_trades else None
    recent_trades = sorted_trades[:30]

    symbols     = list(equity.keys())
    equity_data = {s: equity[s][:500] for s in symbols}

    months     = sorted(monthly.keys())
    monthly_totals = [round(sum(monthly[m].values()), 2) for m in months]

    return render_template(
        "backtest.html",
        now           = datetime.now().strftime("%d %b %Y, %H:%M:%S"),
        has_data      = has_data,
        summary       = summary,
        total_trades  = total_trades,
        total_pnl     = round(total_pnl, 2),
        overall_wr    = round(overall_wr, 1),
        initial_cap   = initial_cap,
        final_cap     = round(final_cap, 2),
        ret_pct       = round(ret_pct, 2),
        best_trade    = best_trade,
        worst_trade   = worst_trade,
        recent_trades = recent_trades,
        equity_data   = json.dumps(equity_data),
        symbols       = json.dumps(symbols),
        months        = json.dumps(months),
        monthly_totals= json.dumps(monthly_totals),
    )


@app.route("/api/backtest/equity")
def api_bt_equity():
    return jsonify(read_bt_equity())

@app.route("/api/backtest/trades")
def api_bt_trades():
    return jsonify(read_bt_trades())


# ─────────────────────────────────────────────
# MARKET INTELLIGENCE — internal helpers
# (returns plain dicts, not Flask responses)
# ─────────────────────────────────────────────

def _fetch_news_data() -> dict:
    """Fetch news sentiment. Returns plain dict."""
    try:
        from data.news_sentiment import get_news_signal
        analyzer = _get_news_analyzer()
        result   = analyzer.get_sentiment()
        signal   = get_news_signal(analyzer)

        headlines = []
        for item in result.recent_headlines[:8]:
            headlines.append({
                "title":     item.title[:90],
                "source":    item.source,
                "score":     item.score,
                "is_hi":     item.is_high_impact,
                "keywords":  item.keywords_found[:3],
                "timestamp": str(item.timestamp)[:16],
            })

        hi_impact = []
        for item in result.high_impact_news[:3]:
            hi_impact.append({
                "title":  item.title[:90],
                "source": item.source,
                "score":  item.score,
            })

        return {
            "overall_score": result.overall_score,
            "signal":        result.signal,
            "confidence":    result.confidence,
            "source_scores": result.source_scores,
            "headlines":     headlines,
            "high_impact":   hi_impact,
            "last_updated":  str(result.last_updated)[:19] if result.last_updated else "",
            "error":         result.error,
            "points":        signal.get("points", 0),
        }
    except Exception as e:
        return {"error": str(e), "signal": "UNAVAILABLE", "overall_score": 0,
                "headlines": [], "high_impact": [], "source_scores": {}}


def _fetch_cpr_data(symbol: str) -> dict:
    """
    Fetch real CPR levels for symbol using Dhan API daily candles.
    Caches result to logs/cpr_cache.json (refreshes once per day).
    Falls back to cached values if API unavailable.
    """
    sym_up = symbol.upper()
    today_str = date.today().isoformat()

    # ── 1. Check in-memory / daily cache ──
    if os.path.exists(CPR_CACHE_PATH):
        try:
            with open(CPR_CACHE_PATH) as f:
                cached = json.load(f)
            sym_cache = cached.get(sym_up, {})
            # Use cache if it was computed today
            if sym_cache.get("cache_date") == today_str and sym_cache.get("pivot"):
                sym_cache["from_cache"] = True
                return sym_cache
        except Exception:
            pass

    # ── 2. Fetch real daily candles from Dhan ──
    try:
        import pandas as pd
        from data.indicators import cpr as compute_cpr

        dhan = _get_dhan_client()
        if dhan is None:
            raise RuntimeError("Dhan client not available — check .env token")

        # Use FUTURES_IDS for historical candles (CPR needs OHLC with OI)
        inst = FUTURES_IDS.get(sym_up)
        if not inst:
            raise ValueError(f"Unknown symbol: {sym_up}")

        # Fetch last 10 trading days (enough for a 2-day CPR calc)
        from_date = (date.today() - timedelta(days=15)).isoformat()
        to_date   = date.today().isoformat()

        candles = dhan.get_daily_candles(
            security_id      = inst["security_id"],
            exchange_segment = inst["exchange"],
            instrument_type  = inst["instrument"],
            expiry_code      = 0,
            from_date        = from_date,
            to_date          = to_date,
        )

        if not candles or len(candles) < 2:
            raise RuntimeError(f"Insufficient daily candles from Dhan (got {len(candles)}) — check security_id in INSTRUMENT_IDS")

        # Build date-indexed DataFrame
        df = pd.DataFrame(candles)
        df["timestamp"] = pd.to_datetime(df["timestamp"])
        df = df.set_index("timestamp").sort_index()
        for col in ["open", "high", "low", "close", "volume"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")

        result = compute_cpr(df)
        if not result:
            raise RuntimeError("CPR calculation returned empty result")

        result["symbol"]     = sym_up
        result["cache_date"] = today_str
        result["from_cache"] = False
        result["data_source"] = "Dhan API (live)"

        # Save to cache
        _save_cpr_cache(sym_up, result)
        return result

    except Exception as e:
        # ── 3. Return last known cache even if stale ──
        if os.path.exists(CPR_CACHE_PATH):
            try:
                with open(CPR_CACHE_PATH) as f:
                    cached = json.load(f)
                sym_cache = cached.get(sym_up, {})
                if sym_cache.get("pivot"):
                    sym_cache["from_cache"] = True
                    sym_cache["cache_stale"] = True
                    sym_cache["error"] = str(e)
                    return sym_cache
            except Exception:
                pass

        return {"symbol": sym_up, "error": str(e), "pivot": None,
                "data_source": "unavailable"}


def _save_cpr_cache(symbol: str, data: dict):
    """Write CPR data to cpr_cache.json."""
    try:
        os.makedirs(os.path.dirname(CPR_CACHE_PATH), exist_ok=True)
        existing = {}
        if os.path.exists(CPR_CACHE_PATH):
            with open(CPR_CACHE_PATH) as f:
                existing = json.load(f)
        existing[symbol] = data
        with open(CPR_CACHE_PATH, "w") as f:
            json.dump(existing, f, indent=2, default=str)
    except Exception:
        pass


# In-memory OC cache (5-min TTL — avoids hammering Dhan API)
_oc_cache: dict = {}
_oc_cache_time: dict = {}
OC_CACHE_TTL = 5 * 60   # 5 minutes

# Expiry cache — stores nearest expiry per symbol (valid for the trading day)
# Avoids calling /optionchain/expirylist on every OC refresh (cuts API calls 50%)
_oc_expiry_cache: dict = {}          # sym → expiry date string
_oc_expiry_cache_date: dict = {}     # sym → calendar date when cached

# NSE India session cache (refreshed every 25 min to keep cookies alive)
_nse_session        = None
_nse_session_time   = 0.0
NSE_SESSION_TTL     = 25 * 60   # 25 minutes


def _get_nse_session():
    """
    Return a requests.Session with valid NSE India cookies.
    Uses full Chrome-like headers including sec-fetch-* which NSE's WAF checks.
    Re-establishes the session every NSE_SESSION_TTL seconds.
    """
    global _nse_session, _nse_session_time
    import requests as _req
    import time as _tm

    now = _tm.time()
    if _nse_session and (now - _nse_session_time) < NSE_SESSION_TTL:
        return _nse_session

    s = _req.Session()
    # Full Chrome 122 headers — NSE's WAF validates sec-ch-ua and sec-fetch-* headers
    s.headers.update({
        "User-Agent":        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                             "AppleWebKit/537.36 (KHTML, like Gecko) "
                             "Chrome/122.0.0.0 Safari/537.36",
        "Accept":            "text/html,application/xhtml+xml,application/xml;"
                             "q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
        "Accept-Language":   "en-IN,en-GB;q=0.9,en-US;q=0.8,en;q=0.7",
        "Accept-Encoding":   "gzip, deflate, br",
        "Connection":        "keep-alive",
        "DNT":               "1",
        "Upgrade-Insecure-Requests": "1",
        "sec-ch-ua":         '"Chromium";v="122", "Not(A:Brand";v="24", "Google Chrome";v="122"',
        "sec-ch-ua-mobile":  "?0",
        "sec-ch-ua-platform": '"Windows"',
        "sec-fetch-dest":    "document",
        "sec-fetch-mode":    "navigate",
        "sec-fetch-site":    "none",
        "sec-fetch-user":    "?1",
    })
    try:
        r1 = s.get("https://www.nseindia.com", timeout=15)
        _tm.sleep(1.0)
        # Update headers to look like a same-origin navigation
        s.headers.update({
            "Referer":        "https://www.nseindia.com/",
            "sec-fetch-site": "same-origin",
            "sec-fetch-dest": "document",
            "sec-fetch-mode": "navigate",
        })
        s.get("https://www.nseindia.com/market-data/live-equity-market", timeout=15)
        _tm.sleep(0.7)
        s.get("https://www.nseindia.com/option-chain", timeout=15)
        _tm.sleep(0.5)
    except Exception as _ex:
        pass  # partial cookies may still work

    _nse_session      = s
    _nse_session_time = now
    return s


def _parse_nse_oc_list(oc_list: list, spot_price: float, nearest_expiry: str) -> dict:
    """
    Core parser — works on both records.data and filtered.data.
    Returns call_wall, put_wall, max_pain, atm_iv etc.
    """
    # Prefer nearest expiry; fall back to all rows
    nearest_data = [x for x in oc_list if x.get("expiryDate") == nearest_expiry]
    if not nearest_data:
        nearest_data = oc_list

    max_call_oi = max_put_oi  = 0
    call_wall   = put_wall    = 0.0
    total_call_oi = total_put_oi = 0
    pain_map: dict = {}

    for item in nearest_data:
        sp   = float(item.get("strikePrice", 0))
        ce   = item.get("CE", {}) or {}
        pe   = item.get("PE", {}) or {}
        c_oi = int(ce.get("openInterest", 0) or 0)
        p_oi = int(pe.get("openInterest", 0) or 0)
        total_call_oi += c_oi
        total_put_oi  += p_oi

        if c_oi > max_call_oi:
            max_call_oi = c_oi;  call_wall = sp
        if p_oi > max_put_oi:
            max_put_oi = p_oi;   put_wall  = sp

        pain_map[sp] = {"c_oi": c_oi, "p_oi": p_oi}

    # Max pain
    max_pain = spot_price
    min_loss = float("inf")
    for ep in pain_map:
        loss = sum(
            max(0, sp - ep) * v["c_oi"] + max(0, ep - sp) * v["p_oi"]
            for sp, v in pain_map.items()
        )
        if loss < min_loss:
            min_loss = loss;  max_pain = ep

    # ATM IV
    if spot_price > 0:
        atm = min(nearest_data, key=lambda x: abs(float(x.get("strikePrice", 0)) - spot_price))
    else:
        atm = nearest_data[len(nearest_data) // 2]

    c_iv      = float((atm.get("CE", {}) or {}).get("impliedVolatility", 0) or 0)
    p_iv      = float((atm.get("PE", {}) or {}).get("impliedVolatility", 0) or 0)
    atm_iv    = round((c_iv + p_iv) / 2, 2) if (c_iv and p_iv) else round(max(c_iv, p_iv), 2)
    atm_strike = float(atm.get("strikePrice", 0))
    pcr_oi     = round(total_put_oi / total_call_oi, 2) if total_call_oi else 0.0

    return {
        "atm_strike":    atm_strike,
        "atm_iv":        atm_iv,
        "call_iv":       round(c_iv, 2),
        "put_iv":        round(p_iv, 2),
        "call_wall":     call_wall,
        "put_wall":      put_wall,
        "max_pain":      round(max_pain, 0),
        "pcr_oi":        pcr_oi,
        "total_call_oi": total_call_oi,
        "total_put_oi":  total_put_oi,
        "rows_used":     len(nearest_data),
    }


def _fetch_spot_from_yahoo(symbol: str) -> float:
    """
    Last-resort spot price from Yahoo Finance (no auth, no cookies needed).
    NIFTY  → ^NSEI,   BANKNIFTY → ^NSEBANK
    """
    import requests as _req
    ticker_map = {"NIFTY": "%5ENSEI", "BANKNIFTY": "%5ENSEBANK"}
    ticker = ticker_map.get(symbol.upper())
    if not ticker:
        return 0.0
    try:
        url = (f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
               f"?interval=1m&range=1d")
        headers = {"User-Agent": "Mozilla/5.0"}
        r = _req.get(url, headers=headers, timeout=10)
        body = r.json()
        price = (body.get("chart", {})
                     .get("result", [{}])[0]
                     .get("meta", {})
                     .get("regularMarketPrice", 0))
        return float(price or 0)
    except Exception:
        return 0.0


def _fetch_oc_from_nse(symbol: str) -> dict:
    """
    Fetch option chain from NSE India free API.
    Strategy:
      1. Try records.data (full OC — all expiries)
      2. Try filtered.data (nearest expiry only — smaller payload)
      3. If only spot price available, return spot-only result
      4. Yahoo Finance as last-resort spot fallback
    """
    sym_up = symbol.upper()
    url    = f"https://www.nseindia.com/api/option-chain-indices?symbol={sym_up}"

    try:
        sess = _get_nse_session()
        # Switch to XHR-style headers for the JSON API call
        api_headers = {
            "Referer":          "https://www.nseindia.com/option-chain",
            "X-Requested-With": "XMLHttpRequest",
            "Accept":           "application/json, text/plain, */*",
            "sec-fetch-dest":   "empty",
            "sec-fetch-mode":   "cors",
            "sec-fetch-site":   "same-origin",
        }
        resp = sess.get(url, headers=api_headers, timeout=15)

        if resp.status_code in (401, 403):
            global _nse_session_time
            _nse_session_time = 0.0
            return {"error": f"NSE blocked ({resp.status_code}) — retrying next poll", "symbol": sym_up}

        if resp.status_code != 200:
            return {"error": f"NSE HTTP {resp.status_code}", "symbol": sym_up}

        # NSE sometimes returns HTML (login redirect) when session is invalid
        ct = resp.headers.get("content-type", "")
        if "html" in ct:
            _nse_session_time = 0.0
            return {"error": "NSE returned HTML (session invalid) — retrying next poll", "symbol": sym_up}

        data     = resp.json()
        records  = data.get("records", {})
        filtered = data.get("filtered", {})

        spot_price     = float(records.get("underlyingValue", 0) or
                               filtered.get("underlyingValue", 0) or 0)
        expiry_dates   = records.get("expiryDates", [])
        nearest_expiry = expiry_dates[0] if expiry_dates else ""

        # Try records.data first (full dataset), then filtered.data
        oc_list = records.get("data") or filtered.get("data") or []

        # Diagnostics in error message
        diag = (f"spot={spot_price} expiries={len(expiry_dates)} "
                f"records_rows={len(records.get('data') or [])} "
                f"filtered_rows={len(filtered.get('data') or [])} "
                f"nearest={nearest_expiry}")

        if not oc_list:
            # Try Yahoo Finance for at least the spot price
            yf_spot = _fetch_spot_from_yahoo(sym_up)
            if yf_spot > 0:
                return {
                    "symbol":     sym_up,
                    "spot_price": round(yf_spot, 2),
                    "source":     "Yahoo Finance (spot only)",
                    "error":      f"NSE OC empty ({diag}); OC levels unavailable",
                    "fetched_at": datetime.now().strftime("%H:%M:%S"),
                }
            return {"error": f"NSE OC empty ({diag})", "symbol": sym_up}

        parsed = _parse_nse_oc_list(oc_list, spot_price, nearest_expiry)

        return {
            "symbol":     sym_up,
            "expiry":     nearest_expiry,
            "spot_price": round(spot_price, 2),
            "fetched_at": datetime.now().strftime("%H:%M:%S"),
            "source":     "NSE India",
            **parsed,
        }

    except Exception as e:
        return {"error": f"NSE OC exception: {e}", "symbol": sym_up}


def _fetch_oc_data(symbol: str) -> dict:
    """
    Fetch live option chain from Dhan and extract:
      - spot_price (LTP of underlying)
      - atm_iv (avg of ATM call+put IV)
      - call_wall (strike with highest call OI = key resistance)
      - put_wall  (strike with highest put OI  = key support)
      - max_pain  (strike where total OI loss is minimised)
      - pcr_oi    (put-call ratio by OI)
    Cached in memory for 5 minutes.
    """
    sym_up = symbol.upper()
    now    = datetime.now().timestamp()

    # Return cached if fresh
    if sym_up in _oc_cache and (now - _oc_cache_time.get(sym_up, 0)) < OC_CACHE_TTL:
        return _oc_cache[sym_up]

    # ── Primary: NSE India (free, no subscription) ──
    nse_result = _fetch_oc_from_nse(sym_up)
    if not nse_result.get("error") and nse_result.get("spot_price", 0) > 0:
        _oc_cache[sym_up]      = nse_result
        _oc_cache_time[sym_up] = now
        return nse_result

    nse_error = nse_result.get("error", "NSE unavailable")

    # ── Fallback: Dhan option chain API ──
    dhan = _get_dhan_client()
    if not dhan:
        return {"symbol": sym_up, "error": f"NSE: {nse_error} | Dhan client unavailable"}

    def _dhan_err(resp: dict, default: str = "") -> str:
        """Extract the most meaningful error string from a Dhan error response."""
        if not resp:
            return "empty response"
        for k in ("remarks", "message", "_error", "errorMessage"):
            if resp.get(k):
                return str(resp[k])[:80]
        # Dhan puts errors in data: {"813": "Invalid SecurityId"}
        d = resp.get("data", {})
        if isinstance(d, dict):
            vals = [str(v) for v in d.values() if v]
            if vals:
                return vals[0][:80]
        http_st = resp.get("_http_status", "")
        return f"HTTP {http_st}" if http_st else (default or "unknown error")

    try:
        # ── Get nearest expiry (cached per trading day) ──
        today_str = date.today().isoformat()
        if (_oc_expiry_cache.get(sym_up) and
                _oc_expiry_cache_date.get(sym_up) == today_str):
            nearest_expiry = _oc_expiry_cache[sym_up]
        else:
            exp_resp = dhan.get_option_chain_all_expiries(sym_up)
            if exp_resp.get("_http_status") or exp_resp.get("_error"):
                return {"symbol": sym_up,
                        "error": f"NSE: {nse_error} | Dhan expiry: {_dhan_err(exp_resp)}"}
            expiries = exp_resp.get("data", [])
            if not expiries:
                return {"symbol": sym_up,
                        "error": f"NSE: {nse_error} | Dhan: no expiries returned"}
            nearest_expiry = expiries[0]
            _oc_expiry_cache[sym_up]      = nearest_expiry
            _oc_expiry_cache_date[sym_up] = today_str

        import time as _oc_time
        _oc_time.sleep(3.2)   # Dhan OC rate limit = 1 req / 3 sec
        raw = dhan.get_option_chain(sym_up, nearest_expiry)
        if not raw or raw.get("_http_status") or raw.get("_error"):
            http_st  = (raw.get("_http_status", "") if raw else "")
            err_str  = _dhan_err(raw, "OC fetch failed")
            return {"symbol": sym_up,
                    "error": f"NSE: {nse_error} | Dhan OC HTTP {http_st}: {err_str}"}

        # Dhan v2 OC actual response format (confirmed):
        #   raw["data"]["last_price"]              → spot price
        #   raw["data"]["oc"]["28000.000000"]      → dict keyed by strike price string
        #   raw["data"]["oc"][strike]["ce"/"pe"]   → call/put data
        #   ce/pe keys: oi, implied_volatility, last_price, greeks, security_id, ...
        data_obj = raw.get("data", {})
        if isinstance(data_obj, list):
            # Defensive: old format fallback (list of strikes)
            return {"symbol": sym_up, "error": "Unexpected OC list format — please report"}

        ul_price = float(data_obj.get("last_price", 0) or 0)
        oc_dict  = data_obj.get("oc", {}) or {}

        if not oc_dict:
            return {"symbol": sym_up, "error": f"NSE: {nse_error} | Dhan: no OC strikes in response"}

        # Fallback spot from middle strike if API didn't give last_price
        if ul_price == 0:
            strikes_sorted = sorted(float(k) for k in oc_dict.keys())
            ul_price = strikes_sorted[len(strikes_sorted) // 2]

        # Aggregate call/put OI across all strikes
        max_call_oi = max_put_oi  = 0
        call_wall   = put_wall    = 0.0
        total_call_oi = total_put_oi = 0
        pain_map: dict = {}

        for strike_str, opts in oc_dict.items():
            sp   = float(strike_str)
            ce   = opts.get("ce", {}) or {}
            pe   = opts.get("pe", {}) or {}
            c_oi = int(ce.get("oi", 0) or 0)
            p_oi = int(pe.get("oi", 0) or 0)
            total_call_oi += c_oi
            total_put_oi  += p_oi

            if c_oi > max_call_oi:
                max_call_oi = c_oi;  call_wall = sp
            if p_oi > max_put_oi:
                max_put_oi  = p_oi;  put_wall  = sp

            pain_map[sp] = {"c_oi": c_oi, "p_oi": p_oi}

        # Max pain — strike where total ITM option losses are minimised
        max_pain = ul_price
        min_loss = float("inf")
        for ep in pain_map:
            loss = sum(
                max(0, sp - ep) * v["c_oi"] + max(0, ep - sp) * v["p_oi"]
                for sp, v in pain_map.items()
            )
            if loss < min_loss:
                min_loss = loss;  max_pain = ep

        # ATM IV — strike closest to spot
        atm_key    = min(oc_dict.keys(), key=lambda k: abs(float(k) - ul_price))
        atm_ce     = oc_dict[atm_key].get("ce", {}) or {}
        atm_pe     = oc_dict[atm_key].get("pe", {}) or {}
        c_iv       = float(atm_ce.get("implied_volatility", 0) or 0)
        p_iv       = float(atm_pe.get("implied_volatility", 0) or 0)
        atm_iv     = round((c_iv + p_iv) / 2, 2) if (c_iv and p_iv) else round(max(c_iv, p_iv), 2)
        atm_strike = float(atm_key)

        pcr_oi = round(total_put_oi / total_call_oi, 2) if total_call_oi else 0.0

        result = {
            "symbol":        sym_up,
            "expiry":        nearest_expiry,
            "spot_price":    round(ul_price, 2),
            "atm_strike":    atm_strike,
            "atm_iv":        atm_iv,
            "call_iv":       round(c_iv, 2),
            "put_iv":        round(p_iv, 2),
            "call_wall":     call_wall,
            "put_wall":      put_wall,
            "max_pain":      round(max_pain, 0),
            "pcr_oi":        pcr_oi,
            "total_call_oi": total_call_oi,
            "total_put_oi":  total_put_oi,
            "fetched_at":    datetime.now().strftime("%H:%M:%S"),
            "source":        "Dhan",
        }

        _oc_cache[sym_up]      = result
        _oc_cache_time[sym_up] = now
        return result

    except Exception as e:
        return {"symbol": sym_up, "error": f"NSE: {nse_error} | Dhan exception: {e}"}


def _fetch_iv_data(symbol: str) -> dict:
    """
    IV data — two-tier:
    1. Live engine cache (iv_cache.json written by main.py) — has full IVP/IVR history
    2. Live Dhan OC fetch — ATM IV only, no percentile history
    """
    sym_up = symbol.upper()

    # Tier 1: engine cache (has IVP/IVR)
    if os.path.exists(IV_CACHE_PATH):
        try:
            with open(IV_CACHE_PATH) as f:
                iv_data = json.load(f)
            mtime    = os.path.getmtime(IV_CACHE_PATH)
            age_mins = (datetime.now().timestamp() - mtime) / 60
            sym_data = iv_data.get(sym_up, {})
            if sym_data and sym_data.get("atm_iv") is not None:
                sym_data["engine_running"] = True
                sym_data["cache_age_mins"] = round(age_mins, 1)
                sym_data["stale"]          = (age_mins > 30) and is_market_open()
                sym_data["source"]         = "engine"
                return sym_data
        except Exception:
            pass

    # Tier 2: live OC fetch (ATM IV only)
    oc = _fetch_oc_data(sym_up)
    if not oc.get("error") and oc.get("atm_iv", 0) > 0:
        atm_iv = oc["atm_iv"]
        if atm_iv > 20:
            regime, hint = "HIGH_IV", "High IV — sell premium (straddle/strangle)"
        elif atm_iv < 12:
            regime, hint = "LOW_IV",  "Low IV — buy options (IV likely to expand)"
        else:
            regime, hint = "NORMAL",  "Normal IV — standard directional strategies"
        return {
            "symbol":        sym_up,
            "atm_iv":        atm_iv,
            "call_iv":       oc.get("call_iv"),
            "put_iv":        oc.get("put_iv"),
            "iv_percentile": None,
            "iv_rank":       None,
            "iv_regime":     regime,
            "hint":          hint,
            "engine_running": False,
            "source":        "live_oc",
            "fetched_at":    oc.get("fetched_at"),
        }

    return {
        "symbol":        sym_up,
        "atm_iv":        None,
        "iv_percentile": None,
        "iv_rank":       None,
        "iv_regime":     "UNKNOWN",
        "hint":          oc.get("error", "Unavailable — check token"),
        "engine_running": False,
        "source":        "unavailable",
    }


# ─────────────────────────────────────────────
# MARKET INTELLIGENCE ROUTES
# ─────────────────────────────────────────────

@app.route("/api/ltp")
def api_ltp():
    """
    Live futures price for NIFTY Apr FUT and BANKNIFTY Apr FUT.
    Primary:  Dhan NSE_FNO LTP (futures contract).
    Fallback: Yahoo Finance index spot (^NSEI / ^NSEBANK).
    """
    result = {}
    dhan = _get_dhan_client()
    if not dhan:
        return jsonify({"error": "Dhan client unavailable — check .env"})

    for i, (sym, inst) in enumerate(INSTRUMENT_IDS.items()):
        if i > 0:
            import time as _time; _time.sleep(0.4)   # brief pause — avoid 429

        ltp    = None
        source = None
        error  = None

        # Attempt 1: Dhan NSE_FNO futures LTP
        try:
            ltp = dhan.get_ltp(inst["exchange"], sym, inst["security_id"])
            if ltp and ltp > 0:
                source = "Dhan FUT"
        except Exception as e:
            error = str(e)

        # Attempt 2: Yahoo Finance (no auth, no rate limits, always works)
        if not ltp:
            try:
                yf = _fetch_spot_from_yahoo(sym)
                if yf and yf > 0:
                    ltp    = yf
                    source = "Yahoo"
                    error  = None
            except Exception as e2:
                error = str(e2)

        # Attempt 3: OC cache spot price (Dhan or NSE, whichever succeeded)
        if not ltp:
            try:
                oc = _fetch_oc_data(sym)
                sp = oc.get("spot_price", 0)
                if sp and sp > 0:
                    ltp    = sp
                    source = oc.get("source", "OC")
                    error  = oc.get("error")
            except Exception as e3:
                error = str(e3)

        result[sym] = {
            "ltp":    ltp,
            "label":  inst.get("label", sym),    # "NIFTY Apr FUT" / "BANKNIFTY Apr FUT"
            "expiry": inst.get("expiry", ""),
            "source": source,
            "error":  error,
        }

    result["fetched_at"] = datetime.now().strftime("%H:%M:%S")
    return jsonify(result)


@app.route("/api/oc_levels/<symbol>")
def api_oc_levels(symbol: str):
    """Option chain levels: call wall (resistance), put wall (support), max pain, ATM IV."""
    return jsonify(_fetch_oc_data(symbol))


@app.route("/api/news")
def api_news():
    """Live news sentiment from RSS feeds (cached 15 min)."""
    return jsonify(_fetch_news_data())


@app.route("/api/cpr/<symbol>")
def api_cpr(symbol: str):
    """
    Compute CPR levels for symbol using real Dhan daily candles.
    Cached once per day to logs/cpr_cache.json.
    """
    return jsonify(_fetch_cpr_data(symbol))


@app.route("/api/iv/<symbol>")
def api_iv(symbol: str):
    """
    Return IV Percentile / IV Rank from live engine cache.
    Written by main.py after each option chain refresh.
    """
    return jsonify(_fetch_iv_data(symbol))


@app.route("/api/market_intel")
def api_market_intel():
    """
    Combined market intelligence: CPR + IV + News for NIFTY and BANKNIFTY.
    Calls internal helper functions directly (not Flask route handlers).
    """
    symbols = ["NIFTY", "BANKNIFTY"]

    # News applies to both indices
    news_data = _fetch_news_data()

    intel = {}
    for sym in symbols:
        intel[sym] = {
            "cpr": _fetch_cpr_data(sym),
            "iv":  _fetch_iv_data(sym),
        }

    return jsonify({"news": news_data, "instruments": intel})


@app.route("/api/debug/dhan/<symbol>")
def api_debug_dhan(symbol: str):
    """Debug endpoint — shows raw Dhan API response for daily candles."""
    sym_up = symbol.upper()
    inst   = INSTRUMENT_IDS.get(sym_up)
    if not inst:
        return jsonify({"error": f"Unknown symbol {sym_up}"})

    dhan = _get_dhan_client()
    if not dhan:
        return jsonify({"error": "Dhan client not available — check .env"})

    from datetime import date, timedelta
    from_date = (date.today() - timedelta(days=15)).isoformat()
    to_date   = date.today().isoformat()

    # Try /charts/historical directly and return raw response
    payload = {
        "securityId":      inst["security_id"],
        "exchangeSegment": inst["exchange"],
        "instrument":      inst["instrument"],
        "expiryCode":      0,
        "oi":              True,
        "fromDate":        from_date,
        "toDate":          to_date,
    }
    try:
        raw = dhan._post("/charts/historical", payload)
        candles = dhan.get_daily_candles(
            security_id=inst["security_id"],
            exchange_segment=inst["exchange"],
            instrument_type=inst["instrument"],
            from_date=from_date,
            to_date=to_date,
        )
        return jsonify({
            "symbol":        sym_up,
            "security_id":   inst["security_id"],
            "from_date":     from_date,
            "to_date":       to_date,
            "raw_keys":      list(raw.keys()) if isinstance(raw, dict) else str(type(raw)),
            "raw_preview":   str(raw)[:500],
            "candles_count": len(candles),
            "candles_sample": candles[-3:] if candles else [],
        })
    except Exception as e:
        return jsonify({"error": str(e), "payload_sent": payload})


@app.route("/api/cpr/refresh/<symbol>")
def api_cpr_refresh(symbol: str):
    """Force refresh CPR by deleting today's cache entry and re-fetching."""
    sym_up = symbol.upper()
    if os.path.exists(CPR_CACHE_PATH):
        try:
            with open(CPR_CACHE_PATH) as f:
                cached = json.load(f)
            if sym_up in cached:
                del cached[sym_up]
            with open(CPR_CACHE_PATH, "w") as f:
                json.dump(cached, f, indent=2, default=str)
        except Exception:
            pass
    return jsonify(_fetch_cpr_data(symbol))


@app.route("/api/debug/full")
def api_debug_full():
    """
    Full diagnostic — call this to see exactly what is and isn't working.
    Visit: http://localhost:8080/api/debug/full
    """
    out = {
        "timestamp":   datetime.now().isoformat(),
        "dhan_token":  {},
        "ltp":         {},
        "oc_expiries": {},
        "oc_data":     {},
        "iv_data":     {},
        "cpr_cache":   {},
    }

    # ── 1. Token / connection ──
    dhan = _get_dhan_client()
    if not dhan:
        out["dhan_token"] = {"status": "FAIL", "msg": "DhanClient not created — check .env CLIENT_ID / ACCESS_TOKEN"}
    else:
        ok, info = dhan.validate_connection()
        out["dhan_token"] = {
            "status": "OK" if ok else "FAIL",
            "msg":    str(info)[:200] if not ok else "Token valid",
            "balance": info.get("availabelBalance", info.get("availableBalance", "N/A")) if ok else "N/A",
        }

    # ── 2. LTP (IDX_I) — one call per symbol with 0.5s gap to avoid 429 ──
    if dhan:
        import time as _dbg_time
        for i, (sym, inst) in enumerate(INSTRUMENT_IDS.items()):
            if i > 0:
                _dbg_time.sleep(0.5)   # avoid Dhan rate-limit 429
            try:
                raw_resp = dhan._post("/marketfeed/ltp", {inst["exchange"]: [int(inst["security_id"])]})
                ltp = dhan.get_ltp(inst["exchange"], sym, inst["security_id"])
                # Also try Yahoo Finance as reference
                yf = _fetch_spot_from_yahoo(sym)
                out["ltp"][sym] = {
                    "ltp":         ltp,
                    "yahoo_spot":  yf,
                    "raw_keys":    list(raw_resp.keys())[:5] if raw_resp else [],
                    "raw_preview": str(raw_resp)[:300],
                }
            except Exception as e:
                out["ltp"][sym] = {"error": str(e)}

    # ── 3. OC expiry list — 3s gap between calls (Dhan rate limit) ──
    if dhan:
        import time as _dbg_oc_time
        for i, sym in enumerate(["NIFTY", "BANKNIFTY"]):
            if i > 0:
                _dbg_oc_time.sleep(3.5)   # Dhan OC rate limit = 1 req / 3 sec
            try:
                resp = dhan.get_option_chain_all_expiries(sym)
                raw_data = resp.get("data")
                expiries = raw_data if isinstance(raw_data, list) else []
                out["oc_expiries"][sym] = {
                    "count":       len(expiries),
                    "nearest":     expiries[0] if expiries else None,
                    "all":         expiries[:5],
                    "raw_keys":    list(resp.keys()) if resp else [],
                    "http_status": resp.get("_http_status"),
                    "api_error":   resp.get("errorMessage",
                                   resp.get("remarks",
                                   resp.get("message",
                                   resp.get("_error")))),
                    "raw_preview": str(resp)[:500],
                }
            except Exception as e:
                import traceback
                out["oc_expiries"][sym] = {"error": str(e), "trace": traceback.format_exc()[-200:]}

    # ── 4. OC data (spot, call wall, etc.) ──
    # Expiry list was already fetched in section 3 above — it's now in _oc_expiry_cache.
    # _fetch_oc_data will skip the expiry re-fetch and go straight to OC data call.
    import time as _dbg_oc2
    _dbg_oc2.sleep(3.5)   # let rate limit window reset after section 3 calls
    for i, sym in enumerate(["NIFTY", "BANKNIFTY"]):
        # Clear only the OC data cache (not expiry cache) so we get fresh OC data
        _oc_cache.pop(sym, None)
        _oc_cache_time.pop(sym, None)
        if i > 0:
            _dbg_oc2.sleep(3.5)   # 3.5s between NIFTY and BANKNIFTY OC data calls
        oc = _fetch_oc_data(sym)
        out["oc_data"][sym] = {k: v for k, v in oc.items() if k not in ("fetched_at",)}

    # ── 5. IV data ──
    for sym in ["NIFTY", "BANKNIFTY"]:
        out["iv_data"][sym] = _fetch_iv_data(sym)

    # ── 6. CPR cache ──
    if os.path.exists(CPR_CACHE_PATH):
        try:
            with open(CPR_CACHE_PATH) as f:
                cpr_cache = json.load(f)
            for sym in ["NIFTY", "BANKNIFTY"]:
                entry = cpr_cache.get(sym, {})
                out["cpr_cache"][sym] = {
                    "cache_date": entry.get("cache_date"),
                    "pivot":      entry.get("pivot"),
                    "r1":         entry.get("r1"),
                    "s1":         entry.get("s1"),
                }
        except Exception as e:
            out["cpr_cache"] = {"error": str(e)}
    else:
        out["cpr_cache"] = {"file": "missing — will be created on first CPR fetch"}

    return jsonify(out)


@app.route("/api/debug/oc_raw")
def api_debug_oc_raw():
    """
    Test Dhan OC with the corrected integer UnderlyingScrip format.
    Dhan error 814 = UnderlyingScrip was sent as string 'NIFTY' instead of int 13.
    Visit: http://localhost:8080/api/debug/oc_raw
    """
    import time as _tm
    dhan = _get_dhan_client()
    if not dhan:
        return jsonify({"error": "Dhan client unavailable"})

    out = {}
    for sym in ["NIFTY", "BANKNIFTY"]:
        sid = dhan._scrip_id(sym)
        out[sym] = {"scrip_id_used": sid, "expiry_list": {}, "oc_sample": {}}

        # Test expiry list — correct field name is UnderlyingSeg (not UnderlyingSegment)
        resp_exp = dhan._post("/optionchain/expirylist", {
            "UnderlyingScrip": sid,
            "UnderlyingSeg":   "IDX_I",
        })
        out[sym]["expiry_list"] = {
            "http_status": resp_exp.get("_http_status"),
            "keys":        list(resp_exp.keys())[:8],
            "preview":     str(resp_exp)[:400],
        }

        # If expiry list succeeded, try full OC
        raw_data = resp_exp.get("data")
        expiries = raw_data if isinstance(raw_data, list) else []
        if expiries:
            _tm.sleep(0.3)
            nearest = expiries[0]
            resp_oc = dhan._post("/optionchain", {
                "UnderlyingScrip": sid,
                "UnderlyingSeg":   "IDX_I",
                "Expiry":          nearest,   # correct key is Expiry, not UnderlyingExpiry
            })
            oc_rows = resp_oc.get("data", [])
            out[sym]["oc_sample"] = {
                "http_status": resp_oc.get("_http_status"),
                "expiry":      nearest,
                "rows":        len(oc_rows),
                "preview":     str(resp_oc)[:500],
            }
        _tm.sleep(0.3)

    return jsonify(out)


@app.route("/api/debug/nse")
def api_debug_nse():
    """
    Detailed NSE session + OC fetch diagnostic.
    Visit: http://localhost:8080/api/debug/nse
    Shows exactly what NSE returns so we can diagnose cookie/anti-bot issues.
    """
    import requests as _req
    import time as _tm

    out = {
        "timestamp":    datetime.now().isoformat(),
        "nse_session":  {},
        "nse_raw":      {},
        "yahoo_spot":   {},
        "nse_parsed":   {},
    }

    # ── 1. Establish fresh NSE session ──
    global _nse_session_time
    _nse_session_time = 0.0   # Force fresh session
    try:
        sess = _get_nse_session()
        cookie_names = [c.name for c in sess.cookies]
        out["nse_session"] = {
            "cookies":      cookie_names,
            "cookie_count": len(cookie_names),
            "has_nsit":     "nsit" in cookie_names,
            "has_nseappid": "nseappid" in cookie_names,
        }
    except Exception as e:
        out["nse_session"] = {"error": str(e)}

    # ── 2. Raw NSE API response for each symbol ──
    for sym in ["NIFTY", "BANKNIFTY"]:
        _tm.sleep(0.5)
        url = f"https://www.nseindia.com/api/option-chain-indices?symbol={sym}"
        try:
            api_headers = {
                "Referer":          "https://www.nseindia.com/option-chain",
                "X-Requested-With": "XMLHttpRequest",
                "Accept":           "application/json, text/plain, */*",
                "sec-fetch-dest":   "empty",
                "sec-fetch-mode":   "cors",
                "sec-fetch-site":   "same-origin",
            }
            r = sess.get(url, headers=api_headers, timeout=15)
            ct = r.headers.get("content-type", "")
            is_json = "json" in ct
            out["nse_raw"][sym] = {
                "http_status":     r.status_code,
                "content_type":    ct,
                "is_json":         is_json,
                "response_len":    len(r.text),
                "preview_200":     r.text[:200],
            }
            if is_json and r.status_code == 200:
                body = r.json()
                rec  = body.get("records", {})
                filt = body.get("filtered", {})
                out["nse_raw"][sym]["parsed"] = {
                    "top_keys":          list(body.keys()),
                    "records_keys":      list(rec.keys())[:10],
                    "underlyingValue":   rec.get("underlyingValue"),
                    "expiryDates":       rec.get("expiryDates", [])[:3],
                    "records_data_len":  len(rec.get("data") or []),
                    "filtered_data_len": len(filt.get("data") or []),
                }
        except Exception as e:
            out["nse_raw"][sym] = {"error": str(e)}

    # ── 3. Yahoo Finance spot (always works) ──
    for sym in ["NIFTY", "BANKNIFTY"]:
        yf = _fetch_spot_from_yahoo(sym)
        out["yahoo_spot"][sym] = {"spot": yf}

    # ── 4. Full parsed OC (using improved logic) ──
    for sym in ["NIFTY", "BANKNIFTY"]:
        _tm.sleep(0.3)
        out["nse_parsed"][sym] = _fetch_oc_from_nse(sym)

    return jsonify(out)


@app.route("/api/debug/find_futures")
def api_debug_find_futures():
    """
    Searches Dhan instrument master for NIFTY and BANKNIFTY April 2026 futures.
    Visit: http://localhost:8080/api/debug/find_futures
    Shows all FUTIDX contracts with their security_id, expiry, and symbol details
    so you can pick the correct IDs to put in INSTRUMENT_IDS.
    """
    import time as _tm

    dhan = _get_dhan_client()
    out = {
        "timestamp":    datetime.now().isoformat(),
        "current_ids":  {k: {"security_id": v["security_id"], "expiry": v["expiry"]}
                         for k, v in INSTRUMENT_IDS.items()},
        "search_results": {},
        "ltp_check": {},
        "recommendation": {},
    }

    target_month = "APR"
    target_year  = "2026"

    for sym in ["NIFTY", "BANKNIFTY"]:
        _tm.sleep(0.3)
        raw = {}
        try:
            raw = dhan.search_instruments(sym)
        except Exception as e:
            out["search_results"][sym] = {"error": str(e)}
            continue

        # Dhan search may return a list or a dict with a data key
        items = []
        if isinstance(raw, list):
            items = raw
        elif isinstance(raw, dict):
            items = raw.get("data", raw.get("instruments", []))
            if not isinstance(items, list):
                items = []

        # Filter: FUTIDX on NSE_FNO only
        futures = [i for i in items if
                   str(i.get("instrumentType", i.get("instrument_type", ""))).upper() == "FUTIDX"
                   and str(i.get("exchangeSegment", i.get("exchange_segment", ""))).upper() in ("NSE_FNO", "NSE FNO")]

        # Sort by expiry
        def _exp(i):
            return str(i.get("expiryDate", i.get("expiry_date", i.get("expiry", ""))))
        futures.sort(key=_exp)

        # Find April 2026 contracts specifically
        apr_2026 = [i for i in futures if
                    target_month in _exp(i).upper() and target_year in _exp(i)]

        # If none matched with "APR", try numeric month (04)
        if not apr_2026:
            apr_2026 = [i for i in futures if _exp(i).startswith("2026-04")]

        out["search_results"][sym] = {
            "total_futures":        len(futures),
            "apr_2026_contracts":   apr_2026,
            "all_future_expiries":  [_exp(i) for i in futures[:12]],
            "raw_sample":           items[:3] if items else raw,
        }

        if apr_2026:
            best = apr_2026[0]
            sid  = str(best.get("securityId", best.get("security_id", best.get("SEM_SMST_SECURITY_ID", ""))))
            exp  = _exp(best)
            out["recommendation"][sym] = {
                "security_id":  sid,
                "expiry":       exp,
                "symbol":       best.get("tradingSymbol", best.get("trading_symbol", best.get("SEM_TRADING_SYMBOL", sym))),
                "current_id":   INSTRUMENT_IDS[sym]["security_id"],
                "needs_update": sid != INSTRUMENT_IDS[sym]["security_id"],
            }

    # Quick LTP check with the IDs we find (if any)
    for sym in ["NIFTY", "BANKNIFTY"]:
        rec = out["recommendation"].get(sym, {})
        sid = rec.get("security_id") or INSTRUMENT_IDS[sym]["security_id"]
        _tm.sleep(0.5)
        try:
            ltp = dhan.get_ltp("NSE_FNO", sym, sid)
            out["ltp_check"][sym] = {
                "security_id_tested": sid,
                "ltp":                ltp,
                "ok":                 ltp is not None and ltp > 0,
            }
        except Exception as e:
            out["ltp_check"][sym] = {"error": str(e)}

    return jsonify(out)


# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────

if __name__ == "__main__":
    # Ensure logs dir exists
    os.makedirs(os.path.join(BASE_DIR, "logs"), exist_ok=True)

    print("\n" + "="*50)
    print("  📊  TRADING DASHBOARD")
    print("="*50)
    print("  Open in browser: http://localhost:8080")
    print("  Auto-refreshes every 30 seconds")
    print("  Press Ctrl+C to stop")
    print("="*50 + "\n")
    app.run(host="0.0.0.0", port=8080, debug=False)
