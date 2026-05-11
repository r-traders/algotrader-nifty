"""
Thursday April 2, 2026 Test
Tries real Dhan API historical data first.
Falls back to realistic mock data if API returns nothing.

Run: python3 test_thursday.py
"""

import sys
import os
import requests
import pandas as pd
import numpy as np
from datetime import datetime
import logging

logging.basicConfig(level=logging.WARNING)

from dotenv import load_dotenv
load_dotenv()

from data.indicators import candles_to_df, add_all_indicators, get_indicator_summary
from strategies.smart_money import SmartMoneyAnalyzer
from strategies.signal_aggregator import SignalAggregator
from data.option_chain_analyzer import OptionChainSignal
from risk.risk_manager import RiskManager

CLIENT_ID    = os.getenv("DHAN_CLIENT_ID", "")
ACCESS_TOKEN = os.getenv("DHAN_ACCESS_TOKEN", "")
HEADERS = {
    "access-token": ACCESS_TOKEN,
    "client-id": CLIENT_ID,
    "Content-Type": "application/json",
}

THURSDAY_DATE = "2026-04-02"
FROM_DATE     = "2026-03-26"

print("\n" + "="*65)
print("  📅  THURSDAY TEST — April 2, 2026")
print("  Trying real Dhan API → fallback to mock if needed")
print("="*65)


# ─────────────────────────────────────────────
# REAL API FETCH
# ─────────────────────────────────────────────

def fetch_real_candles(security_id, exchange, instrument, interval="5"):
    """Try fetching real candles from Dhan API."""
    try:
        payload = {
            "securityId": security_id,
            "exchangeSegment": exchange,
            "instrument": instrument,
            "expiryCode": 0,
            "oi": True,
            "fromDate": FROM_DATE,
            "toDate": THURSDAY_DATE,
        }
        resp = requests.post(
            f"https://api.dhan.co/v2/charts/candle/{interval}",
            headers=HEADERS,
            json=payload,
            timeout=10,
        )
        if resp.status_code == 200:
            data = resp.json()
            raw = data.get("data", data)
            timestamps = raw.get("timestamp", [])
            if timestamps:
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
                return candles, "REAL"
        return [], resp.text[:100]
    except Exception as e:
        return [], str(e)


# ─────────────────────────────────────────────
# MOCK DATA (Thursday Apr 2 realistic values)
# ─────────────────────────────────────────────

THURSDAY_MOCK = {
    "NIFTY":     {"base": 22_161, "close": 22_161, "high": 22_297, "low": 22_075, "trend": "bearish",  "oc_bias": "bearish"},
    "BANKNIFTY": {"base": 47_680, "close": 47_680, "high": 47_890, "low": 47_410, "trend": "bearish",  "oc_bias": "bearish"},
    "RELIANCE":  {"base": 1_268,  "close": 1_268,  "high": 1_281,  "low": 1_251,  "trend": "bearish",  "oc_bias": "neutral"},
    "HDFCBANK":  {"base": 1_705,  "close": 1_705,  "high": 1_722,  "low": 1_694,  "trend": "sideways", "oc_bias": "neutral"},
    "INFY":      {"base": 1_498,  "close": 1_498,  "high": 1_515,  "low": 1_482,  "trend": "bearish",  "oc_bias": "neutral"},
}

def generate_mock_candles(symbol, n=78):
    """Generate realistic 5-min candles using Thursday's known OHLC."""
    m = THURSDAY_MOCK[symbol]
    candles = []
    start = datetime(2026, 4, 2, 9, 15)
    np.random.seed(hash(symbol) % 999)

    base   = m["base"]
    target = m["close"]
    day_h  = m["high"]
    day_l  = m["low"]
    trend  = m["trend"]
    price  = base

    for i in range(n):
        ts = start + pd.Timedelta(minutes=5 * i)
        progress = i / n

        if trend == "bearish":
            drift = np.random.normal(-0.02, 0.12)
            if i < 10: drift -= 0.06
            elif 30 < i < 50: drift += 0.03
        elif trend == "sideways":
            drift = np.random.normal(0.0, 0.08)
        else:
            drift = np.random.normal(0.02, 0.12)

        # Guide price toward day close
        mean_reversion = (target - price) / (base * (n - i + 1)) * 20
        drift += mean_reversion

        change = price * (drift / 100)
        close  = max(day_l, min(day_h, price + change))
        high   = min(day_h, max(price, close) + abs(np.random.normal(0, price * 0.0008)))
        low    = max(day_l, min(price, close) - abs(np.random.normal(0, price * 0.0008)))
        open_  = price + np.random.normal(0, price * 0.0003)
        volume = int(np.random.normal(18000, 4000))

        candles.append({
            "timestamp": ts,
            "open":   round(open_, 2),
            "high":   round(high, 2),
            "low":    round(low, 2),
            "close":  round(close, 2),
            "volume": max(volume, 500),
        })
        price = close

    return candles


def make_oc_signal(symbol, spot, bias):
    atm = round(spot / 100) * 100
    sig = OptionChainSignal(
        symbol=symbol, expiry="2026-04-03",
        spot_price=spot, atm_strike=atm,
        timestamp=str(datetime(2026, 4, 2, 15, 25)),
    )
    if bias == "bearish":
        sig.pcr_oi, sig.pcr_bias = 0.71, "BEARISH"
        sig.call_wall, sig.put_wall = atm + 100, atm - 200
        sig.signal, sig.signal_strength = "BUY_PE", 74.0
        sig.recommended_pe_strike = atm - 100
        sig.recommended_ce_strike = atm + 100
    elif bias == "bullish":
        sig.pcr_oi, sig.pcr_bias = 1.38, "BULLISH"
        sig.call_wall, sig.put_wall = atm + 200, atm - 100
        sig.signal, sig.signal_strength = "BUY_CE", 70.0
        sig.recommended_ce_strike = atm + 100
        sig.recommended_pe_strike = atm - 100
    else:
        sig.pcr_oi, sig.pcr_bias = 1.02, "NEUTRAL"
        sig.signal, sig.signal_strength = "WAIT", 28.0
    sig.atm_call_iv, sig.atm_put_iv = 13.2, 14.8
    sig.iv_skew = -1.6
    sig.max_pain = atm + 50
    sig.max_pain_distance_pct = abs(spot - sig.max_pain) / spot * 100
    return sig


# ─────────────────────────────────────────────
# INSTRUMENTS
# ─────────────────────────────────────────────

INSTRUMENTS = [
    {"symbol": "NIFTY",     "security_id": "13",   "exchange": "NSE_FNO", "instrument": "FUTIDX"},
    {"symbol": "BANKNIFTY", "security_id": "25",   "exchange": "NSE_FNO", "instrument": "FUTIDX"},
    {"symbol": "RELIANCE",  "security_id": "2885", "exchange": "NSE_EQ",  "instrument": "EQUITY"},
    {"symbol": "HDFCBANK",  "security_id": "1333", "exchange": "NSE_EQ",  "instrument": "EQUITY"},
    {"symbol": "INFY",      "security_id": "1594", "exchange": "NSE_EQ",  "instrument": "EQUITY"},
]

smc_analyzer = SmartMoneyAnalyzer()
aggregator   = SignalAggregator()
risk         = RiskManager(total_capital=120_000)
results, trades = [], []

for inst in INSTRUMENTS:
    symbol      = inst["symbol"]
    security_id = inst["security_id"]
    exchange    = inst["exchange"]
    instrument  = inst["instrument"]

    print(f"\n{'─'*65}")
    print(f"  📊  {symbol}")
    print(f"{'─'*65}")

    # Try real API first
    candles, source = fetch_real_candles(security_id, exchange, instrument)
    if candles:
        print(f"  ✅ Real Dhan API data ({len(candles)} candles)")
    else:
        print(f"  ⚠️  API returned no data ({source}) → using mock data")
        candles = generate_mock_candles(symbol)

    df = candles_to_df(candles)
    df = add_all_indicators(df)

    last  = df["close"].iloc[-1]
    open_ = df["open"].iloc[0]
    high  = df["high"].max()
    low   = df["low"].min()
    chg   = (last - open_) / open_ * 100

    print(f"  Price  → Open:{open_:.2f}  High:{high:.2f}  "
          f"Low:{low:.2f}  Close:{last:.2f}  "
          f"({'🟢' if chg >= 0 else '🔴'} {chg:+.2f}%)")

    ind = get_indicator_summary(df)
    print(f"\n  📈 Indicators:")
    print(f"     EMA Trend  : {ind.get('ema_trend','N/A')}")
    print(f"     RSI        : {ind.get('rsi', 0):.1f}  ({ind.get('rsi_signal','N/A')})")
    print(f"     MACD       : {ind.get('macd_cross','N/A')}  (Hist:{ind.get('macd_hist',0):.3f})")
    print(f"     Supertrend : {ind.get('supertrend_dir','N/A')}")
    print(f"     ADX        : {ind.get('adx', 0):.1f}  ({ind.get('trend_strength','N/A')})")
    print(f"     Volume     : {ind.get('vol_ratio', 1):.1f}x  "
          f"{'⚡ SPIKE' if ind.get('vol_spike') else 'Normal'}")

    smc = smc_analyzer.analyze(df, symbol)
    print(f"\n  🧠 Smart Money:")
    print(f"     Structure  : {smc.structure}")
    print(f"     Zone       : {smc.premium_discount}")
    print(f"     Signal     : {smc.signal}  ({smc.signal_type or 'N/A'})")

    oc_signal = None
    if instrument == "FUTIDX":
        m = THURSDAY_MOCK[symbol]
        oc_signal = make_oc_signal(symbol, last, m["oc_bias"])
        print(f"\n  📋 Option Chain:")
        print(f"     PCR        : {oc_signal.pcr_oi:.2f}  ({oc_signal.pcr_bias})")
        print(f"     Call Wall  : {oc_signal.call_wall:.0f}  Put Wall: {oc_signal.put_wall:.0f}")
        print(f"     OC Signal  : {oc_signal.signal}  Strength:{oc_signal.signal_strength:.0f}%")

    final = aggregator.aggregate(
        symbol=symbol, exchange=exchange,
        security_id=security_id, df=df,
        oc_signal=oc_signal, smc_signal=smc, timeframe="5min",
    )

    print(f"\n  {'🎯' if final.is_valid else '⏳'} FINAL SIGNAL:")
    print(f"     Direction  : {final.signal}")
    print(f"     Confidence : {final.confidence:.0f}%  "
          f"{'✅ TRADE' if final.is_valid else '❌ Below threshold'}")
    print(f"     R:R Ratio  : {final.risk_reward}")
    print(f"     Sources    : {final.notes}")

    if final.is_valid:
        print(f"\n  💰 TRADE SETUP:")
        print(f"     Entry      : ₹{final.entry_price:.2f}")
        print(f"     Stop Loss  : ₹{final.stop_loss:.2f}  "
              f"(Risk: ₹{abs(final.entry_price - final.stop_loss):.2f})")
        print(f"     Target 1   : ₹{final.target_1:.2f}  "
              f"(Reward: ₹{abs(final.target_1 - final.entry_price):.2f})")
        print(f"     Target 2   : ₹{final.target_2:.2f}")
        if final.instrument_type in ("CE","PE") and final.strike:
            print(f"     Instrument : {symbol} {final.strike:.0f} {final.instrument_type}  Expiry: 03-Apr-2026")

        allowed, reason = risk.can_trade(final)
        qty = risk.calculate_position_size(final)
        capital = qty * final.entry_price
        print(f"\n  🏦 Risk Check:")
        print(f"     Qty        : {qty}")
        print(f"     Capital    : ₹{capital:,.2f}")
        print(f"     Status     : {'✅ APPROVED' if allowed else f'⛔ BLOCKED: {reason}'}")

        if allowed:
            trades.append(dict(
                symbol=symbol, signal=final.signal,
                instrument=f"{final.instrument_type} {final.strike:.0f}" if final.strike else final.instrument_type,
                entry=final.entry_price, sl=final.stop_loss,
                t1=final.target_1, qty=qty, capital=capital,
                conf=final.confidence, rr=final.risk_reward,
            ))

    results.append(dict(
        symbol=symbol, close=last, chg=chg,
        signal=final.signal, conf=final.confidence,
        rr=final.risk_reward, valid=final.is_valid,
    ))

# ─────────────────────────────────────────────
# SUMMARY
# ─────────────────────────────────────────────
print("\n\n" + "="*65)
print("  📊  THURSDAY APRIL 2 — SUMMARY")
print("="*65)
print(f"  {'Symbol':<12} {'Close':>8} {'Chg':>7} {'Signal':<5} {'Conf':>5} {'R:R':>5}  Trade?")
print("  " + "-"*60)
for r in results:
    print(f"  {r['symbol']:<12} {r['close']:>8.2f} {r['chg']:>+6.2f}% "
          f"{r['signal']:<5} {r['conf']:>4.0f}% {r['rr']:>5.1f}  "
          f"{'✅ YES' if r['valid'] else '⏳ NO'}")

if trades:
    total = sum(t["capital"] for t in trades)
    print(f"\n  🚀 TRADES ({len(trades)} signals fired):")
    print("  " + "-"*60)
    for t in trades:
        print(f"  {t['signal']} {t['symbol']} {t['instrument']}  "
              f"Entry:₹{t['entry']:.2f}  SL:₹{t['sl']:.2f}  "
              f"T1:₹{t['t1']:.2f}  Qty:{t['qty']}  ₹{t['capital']:,.0f}")
    print(f"\n  Total Capital Deployed : ₹{total:,.2f} / ₹1,20,000")
else:
    print("\n  No trades met the 60% confidence threshold.")

print("\n" + "="*65)
print("  ✅  Test complete!")
print("="*65 + "\n")
