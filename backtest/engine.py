"""
Backtesting Engine
Replays Jan 2025 – Apr 2, 2026 historical data through the full
signal pipeline and simulates trade execution with realistic fills.

Features:
  - Fetches real Dhan API candle data (or uses mock if unavailable)
  - Runs Indicators + SMC + Option Chain signals on each bar
  - Simulates entries, SL hits, target hits, trailing stops
  - Tracks full P&L, drawdown, win rate, Sharpe ratio
  - Exports results to CSV for Excel/PDF reporting
"""

import os
import sys
import csv
import json
import logging
import requests
import numpy as np
import pandas as pd
from datetime import datetime, date, timedelta
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass, field
from types import SimpleNamespace
from dotenv import load_dotenv

load_dotenv()
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from data.indicators import candles_to_df, add_all_indicators, get_indicator_summary
from strategies.smart_money import SmartMoneyAnalyzer
from strategies.signal_aggregator import SignalAggregator
from config.settings import RISK_CONFIG

logger = logging.getLogger("Backtest")
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(message)s")

CLIENT_ID    = os.getenv("DHAN_CLIENT_ID", "")
ACCESS_TOKEN = os.getenv("DHAN_ACCESS_TOKEN", "")
HEADERS = {
    "access-token": ACCESS_TOKEN,
    "client-id": CLIENT_ID,
    "Content-Type": "application/json",
}

# ─────────────────────────────────────────────
# BACKTEST CONFIG
# ─────────────────────────────────────────────
@dataclass
class BacktestConfig:
    start_date: str       = "2025-01-01"
    end_date: str         = "2026-04-02"
    initial_capital: float = 120_000.0
    max_risk_per_trade_pct: float = 2.0      # 2% per trade
    max_daily_loss_pct: float     = 3.0
    min_confidence: float          = 50.0   # Higher quality signals only
    min_rr: float                  = 2.0    # Minimum 2:1 R:R
    slippage_pct: float            = 0.05    # 0.05% slippage on entry/exit
    brokerage_per_trade: float     = 40.0    # ₹40 per trade (Dhan flat fee)
    interval: str                  = "15"    # 15-min candles for backtest
    warmup_bars: int               = 50      # Bars needed before first signal


@dataclass
class BacktestTrade:
    trade_id: int
    symbol: str
    direction: str           # BUY / SELL
    instrument_type: str
    entry_date: str
    entry_time: str
    entry_price: float
    stop_loss: float
    target_1: float
    target_2: float
    quantity: int
    exit_date: str     = ""
    exit_time: str     = ""
    exit_price: float  = 0.0
    exit_reason: str   = ""  # TARGET_1 / STOP_LOSS / TRAILING / EOD / MAX_HOLD
    pnl_gross: float   = 0.0
    pnl_net: float     = 0.0
    pnl_pct: float     = 0.0
    confidence: float  = 0.0
    rr_actual: float   = 0.0
    signal_sources: str = ""
    max_adverse: float = 0.0   # MAE - Max Adverse Excursion
    max_favourable: float = 0.0 # MFE - Max Favourable Excursion
    bars_held: int     = 0


@dataclass
class BacktestResult:
    symbol: str
    config: BacktestConfig
    trades: List[BacktestTrade] = field(default_factory=list)
    equity_curve: List[float]   = field(default_factory=list)
    daily_pnl: Dict[str, float] = field(default_factory=dict)

    @property
    def total_trades(self): return len(self.trades)

    @property
    def winners(self): return [t for t in self.trades if t.pnl_net > 0]

    @property
    def losers(self): return [t for t in self.trades if t.pnl_net <= 0]

    @property
    def win_rate(self): return len(self.winners) / self.total_trades * 100 if self.trades else 0

    @property
    def total_pnl(self): return sum(t.pnl_net for t in self.trades)

    @property
    def avg_win(self): return np.mean([t.pnl_net for t in self.winners]) if self.winners else 0

    @property
    def avg_loss(self): return np.mean([t.pnl_net for t in self.losers]) if self.losers else 0

    @property
    def profit_factor(self):
        gross_profit = sum(t.pnl_net for t in self.winners)
        gross_loss   = abs(sum(t.pnl_net for t in self.losers))
        return round(gross_profit / gross_loss, 2) if gross_loss > 0 else float("inf")

    @property
    def max_drawdown(self):
        if not self.equity_curve: return 0
        peak = self.equity_curve[0]
        max_dd = 0
        for val in self.equity_curve:
            if val > peak: peak = val
            dd = (peak - val) / peak * 100
            if dd > max_dd: max_dd = dd
        return round(max_dd, 2)

    @property
    def sharpe_ratio(self):
        if not self.daily_pnl: return 0
        returns = list(self.daily_pnl.values())
        if len(returns) < 2: return 0
        mean_r = np.mean(returns)
        std_r  = np.std(returns)
        return round((mean_r / std_r) * np.sqrt(252), 2) if std_r > 0 else 0

    @property
    def max_consecutive_losses(self):
        max_c = cur_c = 0
        for t in self.trades:
            if t.pnl_net <= 0:
                cur_c += 1
                max_c = max(max_c, cur_c)
            else:
                cur_c = 0
        return max_c


# ─────────────────────────────────────────────
# DATA FETCHER
# ─────────────────────────────────────────────

class DataFetcher:
    """
    Fetches historical candles from Dhan API in chunks.

    Rate-limited: sleeps `CHUNK_SLEEP_SECS` between successive chunk
    fetches. Without this, sequential calls quickly trip Dhan's
    server-side rate limit and revoke the access token (observed
    multiple times in May 2026 — every backtest run killed the token).
    """

    MAX_DAYS_PER_CALL = 90
    CHUNK_SLEEP_SECS = 1.5    # pause between historical-chunk fetches

    def fetch(self, security_id: str, exchange: str, instrument: str,
              start: str, end: str, interval: str = "15") -> pd.DataFrame:
        """Fetch data in chunks and combine into one DataFrame."""
        import time
        all_candles = []
        s = datetime.strptime(start, "%Y-%m-%d").date()
        e = datetime.strptime(end, "%Y-%m-%d").date()
        first_chunk = True

        while s <= e:
            if not first_chunk:
                time.sleep(self.CHUNK_SLEEP_SECS)
            first_chunk = False
            chunk_end = min(s + timedelta(days=self.MAX_DAYS_PER_CALL), e)
            candles = self._fetch_chunk(
                security_id, exchange, instrument,
                s.strftime("%Y-%m-%d"), chunk_end.strftime("%Y-%m-%d"), interval
            )
            all_candles.extend(candles)
            s = chunk_end + timedelta(days=1)

        if not all_candles:
            return pd.DataFrame()

        df = candles_to_df(all_candles)
        df = add_all_indicators(df)
        logger.info(f"Fetched {len(df)} candles for {security_id} ({start} → {end})")
        return df

    def _fetch_chunk(self, security_id, exchange, instrument, from_date, to_date, interval):
        try:
            payload = {
                "securityId":      security_id,
                "exchangeSegment": exchange,
                "instrument":      instrument,
                "interval":        int(interval),  # must be integer, not string
                "expiryCode":      0,
                "oi":              False,
                "fromDate":        from_date,
                "toDate":          to_date,
            }

            resp = requests.post(
                "https://api.dhan.co/v2/charts/intraday",
                headers=HEADERS, json=payload, timeout=15
            )
            if resp.status_code == 200:
                resp_json = resp.json()
                # Dhan intraday API returns flat structure {"open":[...], "timestamp":[...]}
                # Some responses wrap under "data" key — handle both
                raw = resp_json.get("data", resp_json)
                timestamps = raw.get("timestamp", [])
                candles = []
                for i, ts in enumerate(timestamps):
                    candles.append({
                        "timestamp": datetime.fromtimestamp(ts),
                        "open":   raw["open"][i],
                        "high":   raw["high"][i],
                        "low":    raw["low"][i],
                        "close":  raw["close"][i],
                        "volume": raw.get("volume", [0]*len(timestamps))[i],
                    })
                return candles
        except Exception as e:
            logger.warning(f"Chunk fetch failed: {e}")
        return []


# ─────────────────────────────────────────────
# MOCK DATA GENERATOR (fallback)
# ─────────────────────────────────────────────

def generate_mock_historical(symbol: str, start: str, end: str,
                              base_price: float, interval_min: int = 15) -> pd.DataFrame:
    """
    Generate realistic mock OHLCV data with trend, mean reversion and volatility.
    Used when Dhan API doesn't return data for a security ID.
    """
    s = datetime.strptime(start, "%Y-%m-%d")
    e = datetime.strptime(end, "%Y-%m-%d")
    np.random.seed(abs(hash(symbol)) % 9999)

    candles = []
    price   = base_price
    dt      = s

    market_open  = 9 * 60 + 15
    market_close = 15 * 60 + 30

    while dt.date() <= e.date():
        if dt.weekday() >= 5:   # Skip weekends
            dt += timedelta(days=1)
            continue
        mins = market_open
        while mins < market_close:
            ts = dt.replace(hour=mins // 60, minute=mins % 60, second=0)
            # Long term trend (slight upward drift)
            drift = np.random.normal(0.002, 0.15)
            # Mean reversion toward weekly trend
            mean_rev = (base_price * 1.05 - price) / base_price * 0.01
            drift += mean_rev
            # Intraday pattern
            session_pct = (mins - market_open) / (market_close - market_open)
            if session_pct < 0.1:   drift += np.random.normal(0, 0.2)  # Open volatility
            elif session_pct > 0.9: drift += np.random.normal(0, 0.15) # Close volatility

            change = price * (drift / 100)
            close  = price + change
            vol_range = abs(np.random.normal(0, price * 0.002))
            high   = max(price, close) + vol_range
            low    = min(price, close) - vol_range
            open_  = price + np.random.normal(0, price * 0.0005)
            volume = max(int(np.random.normal(12000, 3500)), 100)

            candles.append({
                "timestamp": ts,
                "open":   round(open_, 2),
                "high":   round(high, 2),
                "low":    round(low, 2),
                "close":  round(close, 2),
                "volume": volume,
            })
            price = close
            mins += interval_min

        # Daily price drift (trend component)
        base_price *= np.random.normal(1.0003, 0.008)
        dt += timedelta(days=1)

    df = candles_to_df(candles)
    df = add_all_indicators(df)
    logger.info(f"[MOCK] Generated {len(df)} candles for {symbol}")
    return df


# ─────────────────────────────────────────────
# BACKTESTER
# ─────────────────────────────────────────────

class Backtester:
    def __init__(self, config: BacktestConfig = None):
        self.config    = config or BacktestConfig()
        self.smc        = SmartMoneyAnalyzer()
        self.aggregator = SignalAggregator(threshold=config.min_confidence if config else 38.0)
        self.fetcher    = DataFetcher()

    def run(self, instruments: List[Dict]) -> Dict[str, BacktestResult]:
        """Run backtest for all instruments. Returns results dict keyed by symbol."""
        results = {}
        for inst in instruments:
            symbol = inst["symbol"]
            logger.info(f"\n{'='*50}\nBacktesting {symbol} ({self.config.start_date} → {self.config.end_date})\n{'='*50}")
            result = self._backtest_instrument(inst)
            results[symbol] = result
            self._print_summary(result)
        return results

    def _backtest_instrument(self, inst: Dict) -> BacktestResult:
        result = BacktestResult(symbol=inst["symbol"], config=self.config)
        capital = self.config.initial_capital
        result.equity_curve.append(capital)

        # Fetch data
        df = self.fetcher.fetch(
            inst["security_id"], inst["exchange"], inst["instrument"],
            self.config.start_date, self.config.end_date, self.config.interval
        )
        if df.empty:
            logger.warning(f"No API data for {inst['symbol']} — using mock data")
            df = generate_mock_historical(
                inst["symbol"], self.config.start_date, self.config.end_date,
                inst.get("base_price", 1000), int(self.config.interval)
            )
        if df.empty or len(df) < self.config.warmup_bars + 10:
            logger.error(f"Insufficient data for {inst['symbol']}")
            return result

        trade_id    = 0
        open_trade: Optional[BacktestTrade] = None
        daily_pnl: Dict[str, float] = {}
        daily_trades = 0
        daily_loss_used = 0.0
        last_date = None
        orb_data: Dict[str, dict] = {}   # Opening Range Breakout per day

        for i in range(self.config.warmup_bars, len(df)):
            bar    = df.iloc[i]
            bar_dt = df.index[i]
            bar_date = bar_dt.date().strftime("%Y-%m-%d")
            bar_time = bar_dt.time().hour * 60 + bar_dt.time().minute

            # Reset daily counters on new day
            if bar_date != last_date:
                daily_trades    = 0
                daily_loss_used = 0.0
                last_date       = bar_date
                if bar_date not in daily_pnl:
                    daily_pnl[bar_date] = 0.0
                # Init ORB tracker for new day
                orb_data[bar_date] = {
                    "high": 0.0, "low": float("inf"),
                    "complete": False, "traded": False
                }

            # ── Track Opening Range (9:15 and 9:30 bars) ──
            if bar_time <= 9 * 60 + 30:
                orb_data[bar_date]["high"] = max(orb_data[bar_date]["high"], bar["high"])
                orb_data[bar_date]["low"]  = min(orb_data[bar_date]["low"],  bar["low"])
            elif bar_time == 9 * 60 + 45 and not orb_data[bar_date]["complete"]:
                orb_data[bar_date]["complete"] = True

            # ── Manage open trade ──
            if open_trade:
                open_trade.bars_held += 1
                cur_price = bar["close"]

                # Track MAE / MFE
                if open_trade.direction == "BUY":
                    adverse   = open_trade.entry_price - bar["low"]
                    favourable = bar["high"] - open_trade.entry_price
                else:
                    adverse    = bar["high"] - open_trade.entry_price
                    favourable = open_trade.entry_price - bar["low"]

                open_trade.max_adverse    = max(open_trade.max_adverse, adverse)
                open_trade.max_favourable = max(open_trade.max_favourable, favourable)

                # Check SL / Target
                exited, exit_price, exit_reason = self._check_exit(open_trade, bar)
                if exited:
                    open_trade = self._close_trade(
                        open_trade, exit_price, exit_reason,
                        bar_date, bar_dt.strftime("%H:%M"), result, daily_pnl
                    )
                    capital += open_trade.pnl_net
                    result.equity_curve.append(capital)
                    daily_pnl[bar_date] = daily_pnl.get(bar_date, 0) + open_trade.pnl_net
                    daily_loss_used += min(open_trade.pnl_net, 0)
                    open_trade = None
                    continue

                # EOD square off
                bar_time = bar_dt.time().hour * 60 + bar_dt.time().minute
                if bar_time >= 15 * 60 + 15:
                    open_trade = self._close_trade(
                        open_trade, cur_price, "EOD_SQUAREOFF",
                        bar_date, bar_dt.strftime("%H:%M"), result, daily_pnl
                    )
                    capital += open_trade.pnl_net
                    result.equity_curve.append(capital)
                    daily_pnl[bar_date] = daily_pnl.get(bar_date, 0) + open_trade.pnl_net
                    open_trade = None
                    continue

            # ── Look for new signal ──
            if open_trade is None:
                # Skip if daily limits hit
                if daily_trades >= RISK_CONFIG.max_intraday_trades:
                    continue
                if abs(daily_loss_used) / self.config.initial_capital * 100 >= self.config.max_daily_loss_pct:
                    continue

                # ── Try ORB breakout first (fires after 9:45, once per day) ──
                signal = self._check_orb_signal(orb_data, bar_date, bar, bar_time, inst)

                if signal is None:
                    # ── Time filter: skip 9:15–9:45 open and 14:30–15:30 close ──
                    if bar_time < 9 * 60 + 45 or bar_time > 14 * 60 + 30:
                        continue

                    # ── ADX filter: only trade trending markets (ADX > 25) ──
                    adx_val = bar.get("adx") if hasattr(bar, "get") else bar["adx"] if "adx" in bar.index else None
                    if adx_val is not None and not pd.isna(adx_val) and float(adx_val) < 25:
                        continue

                    hist_df = df.iloc[max(0, i - 200):i + 1].copy()
                    signal  = self._generate_signal(inst, hist_df)

                    # ── VWAP trend alignment ──
                    if signal and signal.signal in ("BUY", "SELL"):
                        vwap_val = bar.get("vwap") if hasattr(bar, "get") else bar["vwap"] if "vwap" in bar.index else None
                        if vwap_val is not None and not pd.isna(vwap_val):
                            vwap_val = float(vwap_val)
                            close_p  = float(bar["close"])
                            if signal.signal == "BUY"  and close_p < vwap_val:
                                signal = None   # Price below VWAP — skip BUY
                            elif signal.signal == "SELL" and close_p > vwap_val:
                                signal = None   # Price above VWAP — skip SELL

                if (signal and
                        signal.signal in ("BUY", "SELL") and
                        signal.confidence >= self.config.min_confidence and
                        signal.entry_price > 0 and
                        signal.stop_loss > 0 and
                        signal.target_1 > 0):
                    if signal.risk_reward < self.config.min_rr:
                        continue

                    # Apply slippage
                    slippage = signal.entry_price * self.config.slippage_pct / 100
                    entry = signal.entry_price + slippage if signal.signal == "BUY" else signal.entry_price - slippage

                    qty = self._calc_qty(capital, entry, signal.stop_loss)
                    if qty <= 0:
                        continue

                    trade_id += 1
                    open_trade = BacktestTrade(
                        trade_id      = trade_id,
                        symbol        = inst["symbol"],
                        direction     = signal.signal,
                        instrument_type = signal.instrument_type or "EQ",
                        entry_date    = bar_date,
                        entry_time    = bar_dt.strftime("%H:%M"),
                        entry_price   = round(entry, 2),
                        stop_loss     = round(signal.stop_loss, 2),
                        target_1      = round(signal.target_1, 2),
                        target_2      = round(signal.target_2, 2),
                        quantity      = qty,
                        confidence    = signal.confidence,
                        signal_sources = signal.notes[:60],
                    )
                    daily_trades += 1

        # Force close any remaining open trade
        if open_trade and len(df) > 0:
            last_bar  = df.iloc[-1]
            last_date = df.index[-1].date().strftime("%Y-%m-%d")
            last_time = df.index[-1].strftime("%H:%M")
            open_trade = self._close_trade(
                open_trade, last_bar["close"], "END_OF_BACKTEST",
                last_date, last_time, result, daily_pnl
            )
            capital += open_trade.pnl_net
            result.equity_curve.append(capital)

        result.daily_pnl = daily_pnl
        return result

    def _generate_signal(self, inst: Dict, df: pd.DataFrame):
        """Run full signal pipeline on a slice of historical data."""
        try:
            smc_signal = self.smc.analyze(df, inst["symbol"])
            return self.aggregator.aggregate(
                symbol      = inst["symbol"],
                exchange    = inst["exchange"],
                security_id = inst["security_id"],
                df          = df,
                oc_signal   = None,   # No historical option chain in backtest
                smc_signal  = smc_signal,
                timeframe   = f"{self.config.interval}min",
            )
        except Exception as e:
            logger.debug(f"Signal error: {e}")
            return None

    def _check_orb_signal(self, orb_data: dict, bar_date: str,
                          bar, bar_time: int, inst: dict):
        """
        Opening Range Breakout (ORB) Strategy.
        Opening range = high/low of 9:15–9:45 first 30 minutes.
        Signal fires on first breakout bar after ORB is complete.
        BUY  when close breaks above ORB high by 0.1%.
        SELL when close breaks below ORB low  by 0.1%.
        SL   at opposite side of range (+10% buffer).
        T1   = 2× risk from entry.
        Confidence = 72% (strong structural signal).
        """
        orb = orb_data.get(bar_date)
        if not orb or not orb.get("complete") or orb.get("traded"):
            return None
        # Only check breakout bars between 9:45 and 11:30 (best ORB window)
        if bar_time <= 9 * 60 + 45 or bar_time > 11 * 60 + 30:
            return None

        orb_high  = orb["high"]
        orb_low   = orb["low"]
        orb_range = orb_high - orb_low
        if orb_range <= 0 or orb_high == 0:
            return None

        close = float(bar["close"])

        # ORB quality filter: range must be 0.3%–1.5% of price (avoid too tight/wide)
        range_pct = orb_range / close * 100
        if range_pct < 0.3 or range_pct > 1.5:
            return None

        # ADX filter: only trade ORB on trending days (ADX > 25)
        adx_val = bar.get("adx") if hasattr(bar, "get") else bar["adx"] if "adx" in bar.index else None
        if adx_val is not None and not pd.isna(adx_val) and float(adx_val) < 25:
            return None

        direction = None
        if close > orb_high * 1.001:    # 0.1% above ORB high → BUY breakout
            direction = "BUY"
        elif close < orb_low * 0.999:   # 0.1% below ORB low  → SELL breakout
            direction = "SELL"

        if not direction:
            return None

        if direction == "BUY":
            sl     = orb_low  - orb_range * 0.10   # SL below ORB low with 10% buffer
            risk   = close - sl
            target = close + 2.0 * risk             # 2:1 R:R target
            t2     = close + 3.0 * risk
        else:
            sl     = orb_high + orb_range * 0.10   # SL above ORB high with 10% buffer
            risk   = sl - close
            target = close - 2.0 * risk
            t2     = close - 3.0 * risk

        if risk <= 0 or target <= 0:
            return None

        rr = abs(target - close) / risk if risk > 0 else 0
        orb_data[bar_date]["traded"] = True          # Only one ORB trade per day

        return SimpleNamespace(
            signal          = direction,
            confidence      = 72.0,
            entry_price     = close,
            stop_loss       = round(sl, 2),
            target_1        = round(target, 2),
            target_2        = round(t2, 2),
            risk_reward     = round(rr, 2),
            instrument_type = inst.get("instrument", "EQ"),
            notes           = (f"ORB:{direction} Range={orb_range:.0f} "
                               f"Hi={orb_high:.0f} Lo={orb_low:.0f}"),
        )

    def _check_exit(self, trade: BacktestTrade, bar) -> Tuple[bool, float, str]:
        """Check SL / target / breakeven trailing stop on current bar."""
        if trade.direction == "BUY":
            # Move SL to breakeven once 50% of target is reached
            half_target = trade.entry_price + (trade.target_1 - trade.entry_price) * 0.5
            if bar["high"] >= half_target and trade.stop_loss < trade.entry_price:
                trade.stop_loss = trade.entry_price   # Trail to breakeven
            if bar["low"] <= trade.stop_loss:
                reason = "BREAKEVEN_STOP" if trade.stop_loss >= trade.entry_price else "STOP_LOSS"
                return True, trade.stop_loss, reason
            if bar["high"] >= trade.target_1:
                return True, trade.target_1, "TARGET_1"
        else:
            # Move SL to breakeven once 50% of target is reached
            half_target = trade.entry_price - (trade.entry_price - trade.target_1) * 0.5
            if bar["low"] <= half_target and trade.stop_loss > trade.entry_price:
                trade.stop_loss = trade.entry_price   # Trail to breakeven
            if bar["high"] >= trade.stop_loss:
                reason = "BREAKEVEN_STOP" if trade.stop_loss <= trade.entry_price else "STOP_LOSS"
                return True, trade.stop_loss, reason
            if bar["low"] <= trade.target_1:
                return True, trade.target_1, "TARGET_1"
        return False, 0.0, ""

    def _close_trade(self, trade: BacktestTrade, exit_price: float, reason: str,
                     exit_date: str, exit_time: str, result: BacktestResult,
                     daily_pnl: Dict) -> BacktestTrade:
        """Finalise a trade with exit details."""
        slippage = exit_price * self.config.slippage_pct / 100
        actual_exit = exit_price - slippage if trade.direction == "BUY" else exit_price + slippage

        if trade.direction == "BUY":
            gross = (actual_exit - trade.entry_price) * trade.quantity
        else:
            gross = (trade.entry_price - actual_exit) * trade.quantity

        net = gross - self.config.brokerage_per_trade * 2  # Entry + exit brokerage

        trade.exit_date   = exit_date
        trade.exit_time   = exit_time
        trade.exit_price  = round(actual_exit, 2)
        trade.exit_reason = reason
        trade.pnl_gross   = round(gross, 2)
        trade.pnl_net     = round(net, 2)
        trade.pnl_pct     = round(net / (trade.entry_price * trade.quantity) * 100, 2)

        if trade.entry_price > 0 and trade.stop_loss > 0:
            risk   = abs(trade.entry_price - trade.stop_loss)
            reward = abs(actual_exit - trade.entry_price)
            trade.rr_actual = round(reward / risk, 2) if risk > 0 else 0

        result.trades.append(trade)
        return trade

    def _calc_qty(self, capital: float, entry: float, sl: float) -> int:
        risk_amount = capital * (self.config.max_risk_per_trade_pct / 100)
        risk_per_unit = abs(entry - sl)
        if risk_per_unit <= 0 or entry <= 0:
            return 0
        return max(int(risk_amount / risk_per_unit), 1)

    def _print_summary(self, result: BacktestResult):
        print(f"\n  📊 {result.symbol} Backtest Results:")
        print(f"     Trades     : {result.total_trades}")
        print(f"     Win Rate   : {result.win_rate:.1f}%")
        print(f"     Net P&L    : ₹{result.total_pnl:,.0f}")
        print(f"     Profit Factor: {result.profit_factor}")
        print(f"     Max Drawdown : {result.max_drawdown:.1f}%")
        print(f"     Sharpe Ratio : {result.sharpe_ratio}")
        print(f"     Max Consec Loss: {result.max_consecutive_losses}")


# ─────────────────────────────────────────────
# EXPORT
# ─────────────────────────────────────────────

def export_to_csv(results: Dict[str, BacktestResult], output_dir: str = "backtest"):
    os.makedirs(output_dir, exist_ok=True)
    all_trades = []
    for symbol, result in results.items():
        for t in result.trades:
            all_trades.append({
                "trade_id":       t.trade_id,
                "symbol":         t.symbol,
                "direction":      t.direction,
                "instrument_type":t.instrument_type,
                "entry_date":     t.entry_date,
                "entry_time":     t.entry_time,
                "entry_price":    t.entry_price,
                "exit_date":      t.exit_date,
                "exit_time":      t.exit_time,
                "exit_price":     t.exit_price,
                "quantity":       t.quantity,
                "stop_loss":      t.stop_loss,
                "target_1":       t.target_1,
                "pnl_gross":      t.pnl_gross,
                "pnl_net":        t.pnl_net,
                "pnl_pct":        t.pnl_pct,
                "exit_reason":    t.exit_reason,
                "confidence":     t.confidence,
                "rr_actual":      t.rr_actual,
                "bars_held":      t.bars_held,
                "signal_sources": t.signal_sources,
            })

    if all_trades:
        trades_file = os.path.join(output_dir, "backtest_trades.csv")
        with open(trades_file, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=all_trades[0].keys())
            writer.writeheader()
            writer.writerows(all_trades)
        logger.info(f"Trades exported: {trades_file}")

    # Summary
    summary_rows = []
    for symbol, r in results.items():
        summary_rows.append({
            "symbol":          symbol,
            "total_trades":    r.total_trades,
            "winners":         len(r.winners),
            "losers":          len(r.losers),
            "win_rate":        round(r.win_rate, 1),
            "total_pnl":       round(r.total_pnl, 2),
            "avg_win":         round(r.avg_win, 2),
            "avg_loss":        round(r.avg_loss, 2),
            "profit_factor":   r.profit_factor,
            "max_drawdown_pct":r.max_drawdown,
            "sharpe_ratio":    r.sharpe_ratio,
            "max_consec_loss": r.max_consecutive_losses,
        })

    if summary_rows:
        summary_file = os.path.join(output_dir, "backtest_summary.csv")
        with open(summary_file, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=summary_rows[0].keys())
            writer.writeheader()
            writer.writerows(summary_rows)
        logger.info(f"Summary exported: {summary_file}")

    # Equity curves
    equity_file = os.path.join(output_dir, "equity_curves.json")
    equity_data = {s: r.equity_curve for s, r in results.items()}
    with open(equity_file, "w") as f:
        json.dump(equity_data, f)

    return all_trades, summary_rows
