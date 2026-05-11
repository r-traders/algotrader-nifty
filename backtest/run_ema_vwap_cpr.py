"""
Run backtest — EMA9/20 + VWAP + CPR Confluence Strategy
NIFTY + BANKNIFTY near-month futures, 5-min candles.

Usage:
    python3 backtest/run_ema_vwap_cpr.py
    python3 backtest/run_ema_vwap_cpr.py --start 2026-01-01 --end 2026-04-30

Outputs to backtest_results/:
    - ema_strategy_trades.csv
    - ema_strategy_summary.csv
    - ema_strategy_equity.json
"""

import os
import sys
import json
import csv
import argparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backtest.engine import BacktestConfig
from backtest.ema_strategy_backtester import EMAStrategyBacktester


# ─────────────────────────────────────────────
# CONTRACT REGISTRY
# Each contract has data from ~3 months before its expiry. To backtest
# Feb→May 2026 use MAY contracts; for live trading (after May expiry) use JUN.
# ─────────────────────────────────────────────
CONTRACTS = {
    "may": [
        {"symbol":"NIFTY",     "security_id":"66071", "exchange":"NSE_FNO",
         "instrument":"FUTIDX", "base_price":22000, "lot_size":65},
        {"symbol":"BANKNIFTY", "security_id":"66068", "exchange":"NSE_FNO",
         "instrument":"FUTIDX", "base_price":47000, "lot_size":30},
    ],
    "jun": [
        {"symbol":"NIFTY",     "security_id":"62329", "exchange":"NSE_FNO",
         "instrument":"FUTIDX", "base_price":22000, "lot_size":65},
        {"symbol":"BANKNIFTY", "security_id":"62326", "exchange":"NSE_FNO",
         "instrument":"FUTIDX", "base_price":47000, "lot_size":30},
    ],
}


def main():
    parser = argparse.ArgumentParser(description="EMA9/20+VWAP+CPR backtest")
    parser.add_argument("--start", default="2025-01-01")
    parser.add_argument("--end",   default="2026-04-30")
    parser.add_argument("--capital", type=float, default=500_000.0)
    parser.add_argument("--min-rr", type=float, default=1.5,
                        help="Minimum risk:reward to take a trade")
    parser.add_argument("--slippage", type=float, default=0.03,
                        help="Per-leg slippage in percent")
    parser.add_argument("--brokerage", type=float, default=50.0,
                        help="Brokerage per leg in INR")
    parser.add_argument("--contract-month", choices=["may","jun"], default="jun",
                        help="Which contract month to fetch ('may' has Feb 25+ history)")
    args = parser.parse_args()
    INSTRUMENTS = CONTRACTS[args.contract_month]

    print("\n" + "=" * 65)
    print("  📊  EMA9/20 + VWAP + CPR CONFLUENCE BACKTEST")
    print("  NIFTY + BANKNIFTY Futures | 5-min | Dhan API / Mock")
    print("=" * 65)

    config = BacktestConfig(
        start_date=args.start,
        end_date=args.end,
        initial_capital=args.capital,
        max_risk_per_trade_pct=1.5,
        max_daily_loss_pct=3.0,
        min_confidence=60.0,
        min_rr=args.min_rr,
        slippage_pct=args.slippage,
        brokerage_per_trade=args.brokerage,
        interval="5",
        warmup_bars=30,
    )

    print(f"\n  Period   : {config.start_date}  →  {config.end_date}")
    print(f"  Capital  : ₹{config.initial_capital:,.0f}")
    print(f"  Interval : {config.interval}-min candles")
    print(f"  Min R:R  : {config.min_rr}")
    print(f"  Slippage : {config.slippage_pct}% per leg")
    print(f"  Brokerage: ₹{config.brokerage_per_trade}/leg")
    print(f"  Symbols  : {', '.join(i['symbol'] for i in INSTRUMENTS)}")
    print("\n  ⏳ Running... (may take a minute for full year)\n")

    backtester = EMAStrategyBacktester(config)
    results = backtester.run(INSTRUMENTS)

    # ─────────────────────────────────────────────
    # EXPORT
    # ─────────────────────────────────────────────
    out_dir = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "backtest_results",
    )
    os.makedirs(out_dir, exist_ok=True)

    # Trades
    all_trades = []
    for symbol, r in results.items():
        for t in r.trades:
            all_trades.append({
                "trade_id": t.trade_id, "symbol": t.symbol,
                "direction": t.direction, "instrument_type": t.instrument_type,
                "entry_date": t.entry_date, "entry_time": t.entry_time,
                "entry_price": t.entry_price, "exit_date": t.exit_date,
                "exit_time": t.exit_time, "exit_price": t.exit_price,
                "quantity": t.quantity, "stop_loss": t.stop_loss,
                "target_1": t.target_1, "target_2": t.target_2,
                "pnl_gross": t.pnl_gross, "pnl_net": t.pnl_net,
                "pnl_pct": t.pnl_pct, "exit_reason": t.exit_reason,
                "rr_actual": t.rr_actual, "bars_held": t.bars_held,
                "max_adverse": round(t.max_adverse, 2),
                "max_favourable": round(t.max_favourable, 2),
                "signal_sources": t.signal_sources,
            })

    if all_trades:
        trades_file = os.path.join(out_dir, "ema_strategy_trades.csv")
        with open(trades_file, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=all_trades[0].keys())
            writer.writeheader()
            writer.writerows(all_trades)
        print(f"\n  💾 Trades  → {trades_file}")

    # Summary
    summary_rows = []
    for symbol, r in results.items():
        summary_rows.append({
            "symbol": symbol,
            "total_trades": r.total_trades,
            "winners": len(r.winners),
            "losers": len(r.losers),
            "win_rate_pct": round(r.win_rate, 1),
            "total_pnl": round(r.total_pnl, 2),
            "avg_win": round(r.avg_win, 2),
            "avg_loss": round(r.avg_loss, 2),
            "profit_factor": r.profit_factor,
            "max_drawdown_pct": r.max_drawdown,
            "sharpe_ratio": r.sharpe_ratio,
            "max_consec_loss": r.max_consecutive_losses,
        })
    if summary_rows:
        summary_file = os.path.join(out_dir, "ema_strategy_summary.csv")
        with open(summary_file, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=summary_rows[0].keys())
            writer.writeheader()
            writer.writerows(summary_rows)
        print(f"  💾 Summary → {summary_file}")

    # Equity curves
    equity_file = os.path.join(out_dir, "ema_strategy_equity.json")
    with open(equity_file, "w") as f:
        json.dump({s: r.equity_curve for s, r in results.items()}, f)
    print(f"  💾 Equity  → {equity_file}")

    # ─────────────────────────────────────────────
    # FINAL SUMMARY
    # ─────────────────────────────────────────────
    print("\n" + "=" * 65)
    print("  📊  OVERALL RESULTS")
    print("=" * 65)
    total_trades = sum(r.total_trades for r in results.values())
    total_pnl = sum(r.total_pnl for r in results.values())
    all_wins = sum(len(r.winners) for r in results.values())
    overall_wr = all_wins / total_trades * 100 if total_trades > 0 else 0
    final_cap = config.initial_capital + total_pnl
    ret_pct = total_pnl / config.initial_capital * 100

    print(f"\n  {'Symbol':<12} {'Trades':>7} {'Win%':>7} {'P&L':>14} {'PF':>6} {'DD%':>7} {'Sharpe':>8}")
    print("  " + "-" * 64)
    for sym, r in results.items():
        print(f"  {sym:<12} {r.total_trades:>7} {r.win_rate:>6.1f}% "
              f"₹{r.total_pnl:>12,.0f} {r.profit_factor:>6.2f} "
              f"{r.max_drawdown:>6.1f}% {r.sharpe_ratio:>8.2f}")
    print("  " + "-" * 64)
    print(f"  {'TOTAL':<12} {total_trades:>7} {overall_wr:>6.1f}%  ₹{total_pnl:>12,.0f}")

    print(f"""
  💰 Starting Capital : ₹{config.initial_capital:>10,.0f}
  💰 Final Capital    : ₹{final_cap:>10,.0f}
  📈 Total Return     : {ret_pct:>+.1f}%
  🏆 Win Rate         : {overall_wr:.1f}%
  📊 Total Trades     : {total_trades}
""")
    print("=" * 65)


if __name__ == "__main__":
    main()
