"""
Run Backtest — Jan 2025 to Apr 2, 2026
NIFTY and BANKNIFTY near-month futures (NSE_FNO / FUTIDX).

NOTE on Dhan API:
  • NSE_IDX INDEX instruments do NOT work with the intraday REST API (DH-905 error).
  • We use near-month FUTIDX contracts instead — these work fine.
  • Security IDs change every month at expiry. Update via find_security_ids.py.
  • Current contracts (Apr 2026 session):
      NIFTY     — 62329 (JUN2026 FUT)
      BANKNIFTY — 62326 (JUN2026 FUT)

Generates: backtest_trades.csv, backtest_summary.csv, PDF report, Excel report
Run: python3 backtest/run_backtest.py
"""

import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backtest.engine import Backtester, BacktestConfig, export_to_csv
from backtest.report import generate_pdf_report, generate_excel_report

print("\n" + "="*65)
print("  📊  BACKTEST ENGINE — Jan 2025 to Apr 2, 2026")
print("  NIFTY + BANKNIFTY Futures | Dhan API | NSE India")
print("="*65)

# ─────────────────────────────────────────────
# INSTRUMENTS — NIFTY & BANKNIFTY FUTURES ONLY
# Update security_ids monthly using find_security_ids.py
# ─────────────────────────────────────────────
INSTRUMENTS = [
    {
        "symbol":      "NIFTY",
        "security_id": "62329",      # NIFTY-JUN2026-FUT — update monthly
        "exchange":    "NSE_FNO",
        "instrument":  "FUTIDX",
        "base_price":  22000,
        "lot_size":    25,
    },
    {
        "symbol":      "BANKNIFTY",
        "security_id": "62326",      # BANKNIFTY-JUN2026-FUT — update monthly
        "exchange":    "NSE_FNO",
        "instrument":  "FUTIDX",
        "base_price":  47000,
        "lot_size":    15,
    },
]

# ─────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────
config = BacktestConfig(
    start_date             = "2025-01-01",
    end_date               = "2026-04-02",
    initial_capital        = 500_000.0,   # ₹5L — appropriate for index futures
    max_risk_per_trade_pct = 1.5,         # Tighter risk for futures
    max_daily_loss_pct     = 3.0,
    min_confidence         = 50.0,        # ADX + VWAP + ORB filters reduce noise
    min_rr                 = 2.0,         # Minimum 2:1 R:R
    slippage_pct           = 0.03,        # Futures are more liquid — tighter slippage
    brokerage_per_trade    = 50.0,        # F&O brokerage slightly higher
    interval               = "15",
    warmup_bars            = 50,
)

print(f"\n  Period   : {config.start_date}  →  {config.end_date}")
print(f"  Capital  : ₹{config.initial_capital:,.0f}")
print(f"  Interval : {config.interval}-min candles")
print(f"  Min Conf : {config.min_confidence}%")
print(f"  Min R:R  : {config.min_rr}")
print(f"  Slippage : {config.slippage_pct}%  |  Brokerage: ₹{config.brokerage_per_trade}/trade")
print(f"  Symbols  : {', '.join(i['symbol'] for i in INSTRUMENTS)}")
print("\n  ⏳ Running... (this may take 1-2 minutes)\n")

# ─────────────────────────────────────────────
# RUN
# ─────────────────────────────────────────────
backtester = Backtester(config)
results    = backtester.run(INSTRUMENTS)

# ─────────────────────────────────────────────
# EXPORT
# ─────────────────────────────────────────────
output_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "backtest_results")
os.makedirs(output_dir, exist_ok=True)

print("\n\n" + "="*65)
print("  💾  Exporting results...")
print("="*65)

trades, summary = export_to_csv(results, output_dir)

# Generate PDF report
pdf_path = generate_pdf_report(results, config, output_dir)
print(f"  ✅ PDF Report : {pdf_path}")

# Generate Excel report
xl_path = generate_excel_report(results, config, trades, output_dir)
print(f"  ✅ Excel Report: {xl_path}")

# ─────────────────────────────────────────────
# FINAL SUMMARY
# ─────────────────────────────────────────────
print("\n" + "="*65)
print("  📊  OVERALL BACKTEST RESULTS  (Jan 2025 – Apr 2, 2026)")
print("="*65)

total_trades = sum(r.total_trades for r in results.values())
total_pnl    = sum(r.total_pnl for r in results.values())
all_wins     = sum(len(r.winners) for r in results.values())
all_losses   = sum(len(r.losers) for r in results.values())
overall_wr   = all_wins / total_trades * 100 if total_trades > 0 else 0
final_cap    = config.initial_capital + total_pnl
ret_pct      = total_pnl / config.initial_capital * 100

print(f"\n  {'Symbol':<12} {'Trades':>7} {'Win%':>7} {'P&L':>12} {'PF':>6} {'DD%':>7} {'Sharpe':>8}")
print("  " + "-"*62)
for sym, r in results.items():
    print(f"  {sym:<12} {r.total_trades:>7} {r.win_rate:>6.1f}% "
          f"₹{r.total_pnl:>10,.0f} {r.profit_factor:>6.2f} "
          f"{r.max_drawdown:>6.1f}% {r.sharpe_ratio:>8.2f}")

print("  " + "-"*62)
print(f"  {'TOTAL':<12} {total_trades:>7} {overall_wr:>6.1f}%  ₹{total_pnl:>10,.0f}")

print(f"""
  💰 Starting Capital : ₹{config.initial_capital:>10,.0f}
  💰 Final Capital    : ₹{final_cap:>10,.0f}
  📈 Total Return     : {ret_pct:>+.1f}%
  🏆 Win Rate         : {overall_wr:.1f}%
  📊 Total Trades     : {total_trades}
""")

print("="*65)
print(f"  ✅  Backtest complete!")
print(f"  📁  Results saved to: backtest_results/")
print("="*65 + "\n")
