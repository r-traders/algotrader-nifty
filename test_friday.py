"""
Friday Test Mode
Fetches Friday (April 4, 2026) historical data from Dhan and runs
the full signal pipeline — indicators, SMC, signal aggregation.
Simulates what the system would have traded on Friday.

Run: python3 test_friday.py
"""

import sys
import logging
from datetime import date, timedelta
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("FridayTest")

from data.dhan_client import DhanClient
from data.indicators import candles_to_df, add_all_indicators, get_indicator_summary
from data.option_chain_analyzer import OptionChainAnalyzer
from strategies.smart_money import SmartMoneyAnalyzer
from strategies.signal_aggregator import SignalAggregator
from risk.risk_manager import RiskManager

# ── Instruments to test ──
# Update security_ids with correct ones from Dhan instruments CSV
TEST_INSTRUMENTS = [
    {"symbol": "NIFTY",     "security_id": "13",    "exchange": "NSE_FNO", "instrument": "FUTIDX"},
    {"symbol": "BANKNIFTY", "security_id": "25",    "exchange": "NSE_FNO", "instrument": "FUTIDX"},
    {"symbol": "RELIANCE",  "security_id": "2885",  "exchange": "NSE_EQ",  "instrument": "EQUITY"},
    {"symbol": "HDFCBANK",  "security_id": "1333",  "exchange": "NSE_EQ",  "instrument": "EQUITY"},
    {"symbol": "INFY",      "security_id": "1594",  "exchange": "NSE_EQ",  "instrument": "EQUITY"},
]

FRIDAY_DATE = "2026-04-04"   # Last Friday
FROM_DATE   = "2026-03-28"   # One week back for indicator warmup


def run_test():
    print("\n" + "="*65)
    print("  📅  FRIDAY TEST MODE — April 4, 2026")
    print("  Replaying Friday's market data through the full pipeline")
    print("="*65 + "\n")

    dhan       = DhanClient()
    oc_analyzer  = OptionChainAnalyzer()
    smc_analyzer = SmartMoneyAnalyzer()
    aggregator   = SignalAggregator()
    risk         = RiskManager(total_capital=500_000)

    results = []

    for inst in TEST_INSTRUMENTS:
        symbol      = inst["symbol"]
        security_id = inst["security_id"]
        exchange    = inst["exchange"]
        instrument  = inst["instrument"]

        print(f"── Analysing {symbol} ──────────────────────────────────")

        # ── 1. Fetch 5-min candles for Friday ──
        candles = dhan.get_historical_data(
            security_id=security_id,
            exchange_segment=exchange,
            instrument_type=instrument,
            from_date=FROM_DATE,
            to_date=FRIDAY_DATE,
            interval="5",
        )

        if not candles:
            print(f"  ⚠️  No candle data returned for {symbol}.")
            print(f"      → Check that security_id '{security_id}' is correct in TEST_INSTRUMENTS.\n")
            continue

        df = candles_to_df(candles)
        df = add_all_indicators(df)

        if df.empty:
            print(f"  ⚠️  DataFrame empty after parsing for {symbol}\n")
            continue

        last_price  = df["close"].iloc[-1]
        day_open    = df["open"].iloc[0]
        day_high    = df["high"].max()
        day_low     = df["low"].min()
        day_change  = (last_price - day_open) / day_open * 100

        print(f"  📊 Price  → Open: {day_open:.2f}  High: {day_high:.2f}  "
              f"Low: {day_low:.2f}  Close: {last_price:.2f}  "
              f"({'🟢' if day_change >= 0 else '🔴'} {day_change:+.2f}%)")

        # ── 2. Indicator summary ──
        ind = get_indicator_summary(df)
        print(f"  📈 EMA Trend : {ind.get('ema_trend','N/A')}")
        print(f"  📉 RSI       : {ind.get('rsi', 0):.1f}  ({ind.get('rsi_signal','N/A')})")
        print(f"  📊 MACD      : {ind.get('macd_cross','N/A')}  (Hist: {ind.get('macd_hist',0):.2f})")
        print(f"  🔁 Supertrend: {ind.get('supertrend_dir','N/A')}")
        print(f"  📦 Volume    : {ind.get('vol_ratio', 1.0):.1f}x avg  "
              f"{'⚡ SPIKE' if ind.get('vol_spike') else ''}")

        # ── 3. SMC Analysis ──
        smc = smc_analyzer.analyze(df, symbol)
        print(f"  🧠 SMC       : Structure={smc.structure}  "
              f"Zone={smc.premium_discount}  Signal={smc.signal} ({smc.signal_type})")

        # ── 4. Option Chain (live fetch for index symbols) ──
        oc_signal = None
        if instrument == "FUTIDX":
            expiry_data = dhan.get_option_chain_all_expiries(symbol)
            expiries = expiry_data.get("data", [])
            if expiries:
                expiry = expiries[0]
                raw_chain = dhan.get_option_chain(symbol, expiry)
                if raw_chain:
                    oc_signal = oc_analyzer.analyze(raw_chain, symbol, expiry)
                    print(f"  📋 OC        : PCR={oc_signal.pcr_oi:.2f} ({oc_signal.pcr_bias})  "
                          f"MaxPain={oc_signal.max_pain:.0f}  "
                          f"CallWall={oc_signal.call_wall:.0f}  PutWall={oc_signal.put_wall:.0f}")
                    print(f"  📋 OC Signal : {oc_signal.signal}  "
                          f"Strength={oc_signal.signal_strength:.0f}%")

        # ── 5. Aggregate signal ──
        final = aggregator.aggregate(
            symbol=symbol,
            exchange=exchange,
            security_id=security_id,
            df=df,
            oc_signal=oc_signal,
            smc_signal=smc,
            timeframe="5min",
        )

        # ── 6. Risk check ──
        trade_allowed, risk_reason = risk.can_trade(final) if final.is_valid else (False, "Signal not valid")

        print(f"\n  {'🎯' if final.is_valid else '⏳'} FINAL SIGNAL : {final.signal}  "
              f"Confidence={final.confidence:.0f}%  R:R={final.risk_reward}")

        if final.is_valid:
            print(f"  💰 Entry     : ₹{final.entry_price:.2f}")
            print(f"  🛑 Stop Loss : ₹{final.stop_loss:.2f}  "
                  f"(Risk: ₹{abs(final.entry_price - final.stop_loss):.2f})")
            print(f"  🎯 Target 1  : ₹{final.target_1:.2f}  "
                  f"(Reward: ₹{abs(final.target_1 - final.entry_price):.2f})")
            print(f"  🎯 Target 2  : ₹{final.target_2:.2f}")
            if final.instrument_type in ("CE", "PE") and final.strike:
                print(f"  📝 Instrument: {symbol} {final.strike:.0f} {final.instrument_type}  "
                      f"Expiry: {final.expiry}")
            print(f"  🔖 Sources   : {final.notes}")

            if trade_allowed:
                qty = risk.calculate_position_size(final)
                capital_used = qty * final.entry_price
                print(f"  ✅ TRADE WOULD EXECUTE  Qty={qty}  "
                      f"Capital Used=₹{capital_used:,.0f}")
            else:
                print(f"  ⛔ Trade blocked by Risk Manager: {risk_reason}")
        else:
            print(f"  ⏳ No trade — confidence too low or R:R insufficient")
            print(f"  🔖 Sources: {final.notes if final.notes else 'No signals aligned'}")

        results.append({
            "symbol": symbol,
            "close": last_price,
            "change_pct": day_change,
            "signal": final.signal,
            "confidence": final.confidence,
            "rr": final.risk_reward,
            "valid": final.is_valid,
        })
        print()

    # ── SUMMARY TABLE ──
    print("="*65)
    print("  📊  FRIDAY SUMMARY")
    print("="*65)
    print(f"  {'Symbol':<12} {'Close':>8} {'Change':>8} {'Signal':<6} {'Conf':>5} {'R:R':>5} {'Trade?'}")
    print("  " + "-"*60)
    for r in results:
        chg_str = f"{r['change_pct']:+.2f}%"
        trade_str = "✅ YES" if r["valid"] else "⏳ NO"
        print(f"  {r['symbol']:<12} {r['close']:>8.2f} {chg_str:>8} "
              f"{r['signal']:<6} {r['confidence']:>4.0f}% {r['rr']:>5} {trade_str}")
    print("="*65)
    print("\n✅ Test complete. Check logs/ folder for detailed trade logs.\n")


if __name__ == "__main__":
    run_test()
