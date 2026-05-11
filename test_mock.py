"""
Mock Friday Test — April 4, 2026
Uses realistic NIFTY & BANKNIFTY 5-min candle data to simulate
the full trading pipeline: Indicators → SMC → Option Chain → Signal → Risk

Run: python3 test_mock.py
"""

import sys
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import logging

logging.basicConfig(level=logging.WARNING)  # Suppress debug logs for clean output

from data.indicators import candles_to_df, add_all_indicators, get_indicator_summary
from strategies.smart_money import SmartMoneyAnalyzer
from strategies.signal_aggregator import SignalAggregator, TradeSignal
from data.option_chain_analyzer import OptionChainAnalyzer, OptionChainSignal
from risk.risk_manager import RiskManager

print("\n" + "="*65)
print("  📅  MOCK FRIDAY TEST — April 4, 2026")
print("  Full pipeline: Data → Indicators → SMC → Signals → Risk")
print("="*65)


# ─────────────────────────────────────────────
# MOCK DATA GENERATOR
# ─────────────────────────────────────────────

def generate_candles(symbol, base_price, trend="bullish", n=78):
    """
    Generate realistic 5-min candles for a full trading day (78 candles = 9:15–3:30).
    trend: bullish / bearish / sideways
    """
    candles = []
    start = datetime(2026, 4, 4, 9, 15)
    price = base_price
    np.random.seed(42 if symbol == "NIFTY" else 99)

    for i in range(n):
        ts = start + timedelta(minutes=5 * i)

        # Trend bias
        if trend == "bullish":
            drift = np.random.normal(0.03, 0.15)
        elif trend == "bearish":
            drift = np.random.normal(-0.03, 0.15)
        else:
            drift = np.random.normal(0.0, 0.12)

        # Intraday pattern: dip at open, rally mid-day
        if i < 10:
            drift -= 0.05   # Slight weakness at open
        elif 10 <= i < 30:
            drift += 0.06   # Morning rally
        elif 30 <= i < 50:
            drift += 0.01   # Consolidation
        elif i >= 60:
            drift += 0.04   # End of day move

        change = price * (drift / 100)
        close = price + change
        high = max(price, close) + abs(np.random.normal(0, price * 0.001))
        low = min(price, close) - abs(np.random.normal(0, price * 0.001))
        open_ = price + np.random.normal(0, price * 0.0005)
        volume = int(np.random.normal(15000, 3000))

        candles.append({
            "timestamp": ts,
            "open": round(open_, 2),
            "high": round(high, 2),
            "low": round(low, 2),
            "close": round(close, 2),
            "volume": max(volume, 1000),
        })
        price = close

    return candles


def generate_option_chain_signal(symbol, spot, bias="bullish"):
    """Create a realistic mock option chain signal."""
    atm = round(spot / 100) * 100

    sig = OptionChainSignal(
        symbol=symbol,
        expiry="2026-04-10",
        spot_price=spot,
        atm_strike=atm,
        timestamp=str(datetime.now()),
    )

    if bias == "bullish":
        sig.pcr_oi = 1.35
        sig.pcr_volume = 1.18
        sig.pcr_bias = "BULLISH"
        sig.call_wall = atm + 200
        sig.put_wall = atm - 100
        sig.signal = "BUY_CE"
        sig.signal_strength = 72.0
        sig.recommended_ce_strike = atm + 100
        sig.recommended_pe_strike = atm - 100
    elif bias == "bearish":
        sig.pcr_oi = 0.72
        sig.pcr_volume = 0.81
        sig.pcr_bias = "BEARISH"
        sig.call_wall = atm + 100
        sig.put_wall = atm - 200
        sig.signal = "BUY_PE"
        sig.signal_strength = 68.0
        sig.recommended_ce_strike = atm + 100
        sig.recommended_pe_strike = atm - 100
    else:
        sig.pcr_oi = 1.05
        sig.pcr_bias = "NEUTRAL"
        sig.signal = "WAIT"
        sig.signal_strength = 30.0

    sig.atm_call_iv = 12.5
    sig.atm_put_iv = 13.8
    sig.iv_skew = -1.3
    sig.max_pain = atm - 50
    sig.max_pain_distance_pct = abs(spot - sig.max_pain) / spot * 100

    return sig


# ─────────────────────────────────────────────
# TEST INSTRUMENTS
# ─────────────────────────────────────────────

MOCK_INSTRUMENTS = [
    {
        "symbol": "NIFTY",
        "base_price": 22_450,
        "trend": "bullish",
        "oc_bias": "bullish",
        "exchange": "NSE_FNO",
        "security_id": "13",
        "instrument_type": "FUTIDX",
    },
    {
        "symbol": "BANKNIFTY",
        "base_price": 48_200,
        "trend": "bearish",
        "oc_bias": "bearish",
        "exchange": "NSE_FNO",
        "security_id": "25",
        "instrument_type": "FUTIDX",
    },
    {
        "symbol": "RELIANCE",
        "base_price": 1_285,
        "trend": "bullish",
        "oc_bias": "neutral",
        "exchange": "NSE_EQ",
        "security_id": "2885",
        "instrument_type": "EQUITY",
    },
    {
        "symbol": "HDFCBANK",
        "base_price": 1_720,
        "trend": "sideways",
        "oc_bias": "neutral",
        "exchange": "NSE_EQ",
        "security_id": "1333",
        "instrument_type": "EQUITY",
    },
    {
        "symbol": "INFY",
        "base_price": 1_520,
        "trend": "bearish",
        "oc_bias": "neutral",
        "exchange": "NSE_EQ",
        "security_id": "1594",
        "instrument_type": "EQUITY",
    },
]


# ─────────────────────────────────────────────
# RUN PIPELINE
# ─────────────────────────────────────────────

smc_analyzer = SmartMoneyAnalyzer()
aggregator   = SignalAggregator()
risk         = RiskManager(total_capital=120_000)   # Match your actual funds

results = []
trades  = []

for inst in MOCK_INSTRUMENTS:
    symbol = inst["symbol"]
    print(f"\n{'─'*65}")
    print(f"  📊  {symbol}")
    print(f"{'─'*65}")

    # Generate candles
    candles = generate_candles(symbol, inst["base_price"], inst["trend"])
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

    # Indicators
    ind = get_indicator_summary(df)
    print(f"\n  📈 Indicators:")
    print(f"     EMA Trend  : {ind.get('ema_trend','N/A')}")
    print(f"     RSI        : {ind.get('rsi', 0):.1f}  ({ind.get('rsi_signal','N/A')})")
    print(f"     MACD       : {ind.get('macd_cross','N/A')}  "
          f"(Hist: {ind.get('macd_hist',0):.3f})")
    print(f"     Supertrend : {ind.get('supertrend_dir','N/A')}")
    print(f"     ADX        : {ind.get('adx', 0):.1f}  ({ind.get('trend_strength','N/A')})")
    print(f"     Volume     : {ind.get('vol_ratio', 1):.1f}x avg  "
          f"{'⚡ SPIKE' if ind.get('vol_spike') else ''}")

    # SMC
    smc = smc_analyzer.analyze(df, symbol)
    print(f"\n  🧠 Smart Money:")
    print(f"     Structure  : {smc.structure}")
    print(f"     Zone       : {smc.premium_discount}")
    print(f"     Signal     : {smc.signal}  ({smc.signal_type or 'N/A'})")
    if smc.nearest_ob:
        ob = smc.nearest_ob
        print(f"     Order Block: {ob.type}  {ob.low:.2f}–{ob.high:.2f}")

    # Option Chain (for index only)
    oc_signal = None
    if inst["instrument_type"] == "FUTIDX":
        oc_signal = generate_option_chain_signal(symbol, last, inst["oc_bias"])
        print(f"\n  📋 Option Chain:")
        print(f"     PCR        : {oc_signal.pcr_oi:.2f}  ({oc_signal.pcr_bias})")
        print(f"     Max Pain   : {oc_signal.max_pain:.0f}  "
              f"(Spot {oc_signal.max_pain_distance_pct:.1f}% away)")
        print(f"     Call Wall  : {oc_signal.call_wall:.0f}  (Resistance)")
        print(f"     Put Wall   : {oc_signal.put_wall:.0f}  (Support)")
        print(f"     IV Skew    : {oc_signal.iv_skew:.1f}  "
              f"(ATM Call IV {oc_signal.atm_call_iv:.1f}% / Put IV {oc_signal.atm_put_iv:.1f}%)")
        print(f"     OC Signal  : {oc_signal.signal}  "
              f"Strength={oc_signal.signal_strength:.0f}%")

    # Aggregate
    final = aggregator.aggregate(
        symbol=symbol,
        exchange=inst["exchange"],
        security_id=inst["security_id"],
        df=df,
        oc_signal=oc_signal,
        smc_signal=smc,
        timeframe="5min",
    )

    print(f"\n  {'🎯' if final.is_valid else '⏳'} AGGREGATED SIGNAL:")
    print(f"     Direction  : {final.signal}")
    print(f"     Confidence : {final.confidence:.0f}%  "
          f"{'✅ TRADE' if final.is_valid else '❌ BELOW THRESHOLD (60%)'}")
    print(f"     R:R Ratio  : {final.risk_reward}  "
          f"{'✅' if final.risk_reward >= 1.5 else '❌ Below min 1.5'}")
    print(f"     Sources    : {final.notes}")

    if final.is_valid:
        print(f"\n  💰 TRADE SETUP:")
        print(f"     Entry      : ₹{final.entry_price:.2f}")
        print(f"     Stop Loss  : ₹{final.stop_loss:.2f}  "
              f"(Risk: ₹{abs(final.entry_price - final.stop_loss):.2f})")
        print(f"     Target 1   : ₹{final.target_1:.2f}  "
              f"(Reward: ₹{abs(final.target_1 - final.entry_price):.2f})")
        print(f"     Target 2   : ₹{final.target_2:.2f}")
        if final.instrument_type in ("CE", "PE") and final.strike:
            print(f"     Instrument : {symbol} {final.strike:.0f} {final.instrument_type}  "
                  f"Expiry: 10-Apr-2026")

        allowed, reason = risk.can_trade(final)
        qty = risk.calculate_position_size(final)
        capital = qty * final.entry_price
        print(f"\n  🏦 RISK CHECK:")
        print(f"     Qty        : {qty} lots/shares")
        print(f"     Capital    : ₹{capital:,.2f}")
        print(f"     Status     : {'✅ APPROVED — Would Execute' if allowed else f'⛔ BLOCKED: {reason}'}")

        if allowed:
            trades.append({
                "symbol": symbol,
                "signal": final.signal,
                "instrument": f"{final.instrument_type} {final.strike:.0f}" if final.strike else final.instrument_type,
                "entry": final.entry_price,
                "sl": final.stop_loss,
                "t1": final.target_1,
                "qty": qty,
                "capital": capital,
                "confidence": final.confidence,
                "rr": final.risk_reward,
            })

    results.append({
        "symbol": symbol,
        "close": last,
        "chg": chg,
        "signal": final.signal,
        "conf": final.confidence,
        "rr": final.risk_reward,
        "valid": final.is_valid,
    })


# ─────────────────────────────────────────────
# FINAL SUMMARY
# ─────────────────────────────────────────────

print("\n\n" + "="*65)
print("  📊  FRIDAY SIGNAL SUMMARY")
print("="*65)
print(f"  {'Symbol':<12} {'Close':>8} {'Chg':>7} {'Signal':<5} {'Conf':>5} {'R:R':>5}  Trade?")
print("  " + "-"*60)
for r in results:
    chg_str = f"{r['chg']:+.2f}%"
    trade_str = "✅ YES" if r["valid"] else "⏳ NO"
    print(f"  {r['symbol']:<12} {r['close']:>8.2f} {chg_str:>7} "
          f"{r['signal']:<5} {r['conf']:>4.0f}% {r['rr']:>5.1f}  {trade_str}")

if trades:
    print(f"\n  🚀  TRADES THAT WOULD HAVE BEEN PLACED ({len(trades)}):")
    print("  " + "-"*60)
    total_capital = sum(t["capital"] for t in trades)
    for t in trades:
        print(f"  {t['signal']} {t['symbol']} {t['instrument']}  "
              f"Entry:₹{t['entry']:.2f}  SL:₹{t['sl']:.2f}  T1:₹{t['t1']:.2f}  "
              f"Qty:{t['qty']}  Capital:₹{t['capital']:,.0f}")
    print(f"\n  Total Capital Deployed : ₹{total_capital:,.2f}")
    print(f"  Available Funds        : ₹1,20,000")
    print(f"  Remaining Capital      : ₹{120000 - total_capital:,.2f}")
else:
    print("\n  No trades met the confidence threshold on this data.")

print("\n" + "="*65)
print("  ✅  Mock test complete!")
print("  ℹ️   This used simulated data. On Monday, real Dhan API data")
print("       will be used for live paper trading.")
print("="*65 + "\n")
