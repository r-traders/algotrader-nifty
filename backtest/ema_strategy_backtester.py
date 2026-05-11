"""
Backtester for the EMA9/20 + VWAP + CPR confluence strategy.

Drives entries/exits from `EMAVWAPCPRStrategy` instead of the legacy
SignalAggregator pipeline. Reuses data structures, fetcher, mock
generator, and exporters from `backtest.engine`.

Key differences vs. legacy backtester:
  - 5-min candles (not 15-min)
  - Lot-aware position sizing (lots × lot_size)
  - Entry windows: 09:30–11:15 and 13:30–14:30 IST
  - Strategy-driven exits (EMA flip, VWAP cross) AND hard SL/T1
  - EOD square-off at 15:15
"""

import os
import sys
import logging
import time
import requests
from typing import Dict, List, Optional, Tuple
from datetime import datetime, timedelta, time as dt_time
from types import SimpleNamespace

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backtest.engine import (
    BacktestConfig, BacktestTrade, BacktestResult,
    DataFetcher, generate_mock_historical,
)
from strategies.ema_vwap_cpr import EMAVWAPCPRStrategy
from config.settings import EMA_VWAP_CPR_CONFIG
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))


def fetch_daily_history(security_id: str, exchange: str, instrument: str,
                         start: str, end: str) -> pd.DataFrame:
    """
    Fetch daily OHLCV history from Dhan's /v2/charts/historical endpoint.
    Returns a DataFrame indexed by date (one bar per trading day).
    Note: when passed a futures-contract sid, this endpoint returns the
    UNDERLYING INDEX history, not the specific contract.
    """
    headers = {
        "access-token": os.getenv("DHAN_ACCESS_TOKEN", ""),
        "client-id":    os.getenv("DHAN_CLIENT_ID", ""),
        "Content-Type": "application/json",
    }
    payload = {
        "securityId":      security_id,
        "exchangeSegment": exchange,
        "instrument":      instrument,
        "expiryCode":      0,
        "oi":              False,
        "fromDate":        start,
        "toDate":          end,
    }
    try:
        r = requests.post(
            "https://api.dhan.co/v2/charts/historical",
            headers=headers, json=payload, timeout=30,
        )
        if r.status_code != 200:
            logging.warning(f"Daily fetch HTTP {r.status_code}: {r.text[:200]}")
            return pd.DataFrame()
        body = r.json()
        raw = body.get("data", body)
        ts = raw.get("timestamp", [])
        if not ts:
            return pd.DataFrame()
        df = pd.DataFrame({
            "open":   raw.get("open", []),
            "high":   raw.get("high", []),
            "low":    raw.get("low", []),
            "close":  raw.get("close", []),
            "volume": raw.get("volume", [0] * len(ts)),
        }, index=pd.to_datetime([datetime.fromtimestamp(t) for t in ts]))
        return df.sort_index()
    except Exception as e:
        logging.warning(f"Daily fetch error: {e}")
        return pd.DataFrame()

logger = logging.getLogger("EMABacktest")


class EMAStrategyBacktester:
    """Bar-by-bar backtester for the EMA-VWAP-CPR strategy."""

    EOD_SQUAREOFF_MIN = 15 * 60 + 15   # 15:15 — flatten before close

    def __init__(self, config: BacktestConfig = None):
        self.config = config or BacktestConfig(interval="5", warmup_bars=30)
        # Force 5-min interval for this strategy
        if self.config.interval != "5":
            logger.warning(
                f"Overriding config.interval={self.config.interval} → '5' "
                f"(EMA-VWAP-CPR is a 5-min strategy)"
            )
            self.config.interval = "5"
        self.strategy = EMAVWAPCPRStrategy()
        self.fetcher = DataFetcher()

    def run(self, instruments: List[Dict]) -> Dict[str, BacktestResult]:
        results = {}
        for inst in instruments:
            symbol = inst["symbol"]
            logger.info(
                f"\n{'='*55}\n"
                f"Backtesting {symbol} (EMA9/20 + VWAP + CPR)\n"
                f"{self.config.start_date} → {self.config.end_date}\n"
                f"{'='*55}"
            )
            # Fresh strategy instance per instrument to clear dedup state
            self.strategy = EMAVWAPCPRStrategy()
            results[symbol] = self._backtest_instrument(inst)
            self._print_summary(results[symbol])
        return results

    def _backtest_instrument(self, inst: Dict) -> BacktestResult:
        result = BacktestResult(symbol=inst["symbol"], config=self.config)
        capital = self.config.initial_capital
        result.equity_curve.append(capital)

        # Fetch data
        df = self.fetcher.fetch(
            inst["security_id"], inst["exchange"], inst["instrument"],
            self.config.start_date, self.config.end_date, self.config.interval,
        )
        if df.empty:
            logger.warning(f"No API data for {inst['symbol']} — using mock data")
            df = generate_mock_historical(
                inst["symbol"], self.config.start_date, self.config.end_date,
                inst.get("base_price", 22000), int(self.config.interval),
            )
        if df.empty or len(df) < self.config.warmup_bars + 10:
            logger.error(f"Insufficient data for {inst['symbol']}")
            return result

        # ── Fetch daily history for the daily-trend filter ──
        # The /v2/charts/historical endpoint returns the underlying INDEX
        # history when given a futures sid — perfect for trend context.
        if self.strategy.cfg.require_daily_trend_alignment:
            daily_from = (datetime.strptime(self.config.start_date, "%Y-%m-%d")
                          - timedelta(days=self.strategy.cfg.daily_lookback_days)
                          ).strftime("%Y-%m-%d")
            daily_to = self.config.end_date
            logger.info(f"Fetching daily history for {inst['symbol']} ({daily_from} → {daily_to})...")
            time.sleep(1.5)  # rate-limit between symbols' daily fetches
            daily_df = fetch_daily_history(
                inst["security_id"], inst["exchange"], inst["instrument"],
                daily_from, daily_to,
            )
            if daily_df.empty:
                logger.warning(f"No daily data for {inst['symbol']} — daily filter will pass everything")
            else:
                logger.info(f"  {len(daily_df)} daily bars  "
                            f"{daily_df.index[0].strftime('%Y-%m-%d')} → {daily_df.index[-1].strftime('%Y-%m-%d')}")
                self.strategy.set_daily_data(inst["symbol"], daily_df)

        lot_size = inst.get("lot_size", 1)

        trade_id = 0
        open_trade: Optional[BacktestTrade] = None
        daily_pnl: Dict[str, float] = {}
        daily_loss_used = 0.0
        last_date = None

        for i in range(self.config.warmup_bars, len(df)):
            bar = df.iloc[i]
            bar_dt = df.index[i]
            bar_date = bar_dt.date().strftime("%Y-%m-%d")
            bar_time = bar_dt.time().hour * 60 + bar_dt.time().minute

            # Reset daily counters on new day
            if bar_date != last_date:
                daily_loss_used = 0.0
                last_date = bar_date
                daily_pnl.setdefault(bar_date, 0.0)

            # ───── Manage open trade ─────
            if open_trade is not None:
                open_trade.bars_held += 1

                # Track MAE / MFE
                if open_trade.direction == "BUY":
                    adverse = open_trade.entry_price - bar["low"]
                    favourable = bar["high"] - open_trade.entry_price
                else:
                    adverse = bar["high"] - open_trade.entry_price
                    favourable = open_trade.entry_price - bar["low"]
                open_trade.max_adverse = max(open_trade.max_adverse, adverse)
                open_trade.max_favourable = max(open_trade.max_favourable, favourable)

                hist_df_pos = df.iloc[max(0, i - 200):i + 1]
                pos_proxy = SimpleNamespace(
                    symbol=open_trade.symbol, direction=open_trade.direction
                )

                # ── V2: Move SL to breakeven after 1:1 move (journal exit #8) ──
                if not open_trade.breakeven_moved:
                    risk_pts = abs(open_trade.entry_price - open_trade.stop_loss)
                    trigger = self.strategy.cfg.breakeven_rr_trigger
                    if open_trade.direction == "BUY":
                        if bar["high"] >= open_trade.entry_price + trigger * risk_pts:
                            open_trade.stop_loss = open_trade.entry_price
                            open_trade.breakeven_moved = True
                    else:
                        if bar["low"] <= open_trade.entry_price - trigger * risk_pts:
                            open_trade.stop_loss = open_trade.entry_price
                            open_trade.breakeven_moved = True

                # ── V2: RSI extreme partial book (journal exit #5) ──
                if not open_trade.partial_booked:
                    rsi_extreme, rsi_reason = self.strategy.should_partial_at_rsi_extreme(
                        pos_proxy, hist_df_pos
                    )
                    if rsi_extreme:
                        self._book_partial(
                            open_trade, float(bar["close"]), rsi_reason,
                        )

                # ── V2: T1 partial book + start EMA9 trail (journal exits #1, #2) ──
                if not open_trade.partial_booked:
                    t1_hit = (
                        (open_trade.direction == "BUY"  and bar["high"] >= open_trade.target_1) or
                        (open_trade.direction == "SELL" and bar["low"]  <= open_trade.target_1)
                    )
                    if t1_hit:
                        self._book_partial(
                            open_trade, open_trade.target_1, "T1_PARTIAL",
                        )

                # ── V2: Trail remaining 50% by EMA 9 (journal exit #2) ──
                if open_trade.partial_booked:
                    ema9_now = self.strategy.current_ema9(hist_df_pos)
                    if ema9_now is not None:
                        if open_trade.direction == "BUY":
                            # Tighten only — never loosen the stop
                            if ema9_now > open_trade.stop_loss:
                                open_trade.stop_loss = round(ema9_now, 2)
                        else:
                            if ema9_now < open_trade.stop_loss:
                                open_trade.stop_loss = round(ema9_now, 2)

                # ── V2: RSI divergence → full exit of remainder (journal exit #6) ──
                div_hit, div_reason = self.strategy.detect_rsi_divergence(
                    pos_proxy, hist_df_pos
                )
                if div_hit:
                    closed_dir = open_trade.direction
                    open_trade = self._close_trade(
                        open_trade, float(bar["close"]), f"RSI_DIV:{div_reason}",
                        bar_date, bar_dt.strftime("%H:%M"), result,
                    )
                    capital += open_trade.pnl_net
                    daily_pnl[bar_date] = daily_pnl.get(bar_date, 0) + open_trade.pnl_net
                    daily_loss_used += min(open_trade.pnl_net, 0)
                    result.equity_curve.append(capital)
                    self.strategy.clear_exit_state(inst["symbol"], closed_dir)
                    self.strategy.clear_setup_state(inst["symbol"])
                    open_trade = None
                    continue

                # 1. Hard SL / Target hit (intra-bar)
                exited, exit_price, exit_reason = self._check_hard_exit(open_trade, bar)
                if exited:
                    closed_dir = open_trade.direction
                    open_trade = self._close_trade(
                        open_trade, exit_price, exit_reason,
                        bar_date, bar_dt.strftime("%H:%M"), result,
                    )
                    capital += open_trade.pnl_net
                    daily_pnl[bar_date] = daily_pnl.get(bar_date, 0) + open_trade.pnl_net
                    daily_loss_used += min(open_trade.pnl_net, 0)
                    result.equity_curve.append(capital)
                    self.strategy.clear_exit_state(inst["symbol"], closed_dir)
                    self.strategy.clear_setup_state(inst["symbol"])
                    open_trade = None
                    continue

                # 2. EOD square-off
                if bar_time >= self.EOD_SQUAREOFF_MIN:
                    closed_dir = open_trade.direction
                    open_trade = self._close_trade(
                        open_trade, float(bar["close"]), "EOD_SQUAREOFF",
                        bar_date, bar_dt.strftime("%H:%M"), result,
                    )
                    capital += open_trade.pnl_net
                    daily_pnl[bar_date] = daily_pnl.get(bar_date, 0) + open_trade.pnl_net
                    result.equity_curve.append(capital)
                    self.strategy.clear_exit_state(inst["symbol"], closed_dir)
                    self.strategy.clear_setup_state(inst["symbol"])
                    open_trade = None
                    continue

                # 3. Strategy exit (EMA flip / VWAP cross) — checked on bar close
                hist_df = df.iloc[max(0, i - 200):i + 1]
                pos_proxy = SimpleNamespace(
                    symbol=open_trade.symbol, direction=open_trade.direction
                )
                should_exit, reason = self.strategy.should_exit(pos_proxy, hist_df)
                if should_exit:
                    closed_dir = open_trade.direction
                    open_trade = self._close_trade(
                        open_trade, float(bar["close"]), f"STRATEGY:{reason}",
                        bar_date, bar_dt.strftime("%H:%M"), result,
                    )
                    capital += open_trade.pnl_net
                    daily_pnl[bar_date] = daily_pnl.get(bar_date, 0) + open_trade.pnl_net
                    daily_loss_used += min(open_trade.pnl_net, 0)
                    result.equity_curve.append(capital)
                    self.strategy.clear_exit_state(inst["symbol"], closed_dir)
                    self.strategy.clear_setup_state(inst["symbol"])
                    open_trade = None
                    continue

            # ───── Look for new signal ─────
            if open_trade is None:
                # Daily loss guard
                if abs(daily_loss_used) / self.config.initial_capital * 100 \
                        >= self.config.max_daily_loss_pct:
                    continue

                # Don't enter near EOD (also enforced by strategy windows)
                if bar_time >= self.EOD_SQUAREOFF_MIN:
                    continue

                # Pass a window of historical data to the strategy
                hist_df = df.iloc[max(0, i - 200):i + 1]
                signal = self.strategy.evaluate(
                    symbol=inst["symbol"],
                    exchange=inst["exchange"],
                    security_id=inst["security_id"],
                    df=hist_df,
                    now=bar_dt.to_pydatetime(),
                )

                if signal.signal not in ("BUY", "SELL"):
                    continue
                if signal.risk_reward < self.config.min_rr:
                    continue

                # Apply slippage
                slippage = signal.entry_price * self.config.slippage_pct / 100
                entry = (signal.entry_price + slippage
                         if signal.signal == "BUY"
                         else signal.entry_price - slippage)

                # Lot-aware sizing
                lots = self.strategy.recommend_lots(
                    capital=capital,
                    entry_price=entry,
                    stop_loss=signal.stop_loss,
                    lot_size=lot_size,
                    max_risk_pct=EMA_VWAP_CPR_CONFIG.max_risk_per_trade_pct,
                    max_lots=EMA_VWAP_CPR_CONFIG.max_lots_per_trade,
                )
                if lots <= 0:
                    continue
                qty = lots * lot_size

                trade_id += 1
                open_trade = BacktestTrade(
                    trade_id=trade_id,
                    symbol=inst["symbol"],
                    direction=signal.signal,
                    instrument_type="FUT",
                    entry_date=bar_date,
                    entry_time=bar_dt.strftime("%H:%M"),
                    entry_price=round(entry, 2),
                    stop_loss=round(signal.stop_loss, 2),
                    target_1=round(signal.target_1, 2),
                    target_2=round(signal.target_2, 2),
                    quantity=qty,
                    confidence=signal.confidence,
                    signal_sources=signal.notes[:80],
                )
                # V2 trade-lifecycle state (attached as attributes since
                # BacktestTrade dataclass is shared with the legacy engine).
                open_trade.original_qty = qty
                open_trade.partial_booked = False
                open_trade.partial_pnl = 0.0
                open_trade.partial_reason = ""
                open_trade.breakeven_moved = False
                # Fresh streak for new position
                self.strategy.clear_exit_state(inst["symbol"], signal.signal)

        # Force-close any trailing open trade
        if open_trade is not None and len(df) > 0:
            last_bar = df.iloc[-1]
            last_date = df.index[-1].date().strftime("%Y-%m-%d")
            last_time = df.index[-1].strftime("%H:%M")
            closed_dir = open_trade.direction
            open_trade = self._close_trade(
                open_trade, float(last_bar["close"]), "END_OF_BACKTEST",
                last_date, last_time, result,
            )
            capital += open_trade.pnl_net
            result.equity_curve.append(capital)
            self.strategy.clear_exit_state(inst["symbol"], closed_dir)

        result.daily_pnl = daily_pnl
        return result

    # ─────────────────────────────────────────────
    # EXIT CHECKS
    # ─────────────────────────────────────────────

    def _check_hard_exit(self, trade: BacktestTrade, bar) -> Tuple[bool, float, str]:
        """
        Intra-bar SL / T1 / T2 hit check using bar's H/L. Conservative ordering:
        when both SL and target would fire on the same bar, assume SL first.

        After partial booking, T1/T2 no longer trigger full exits — the
        remaining half is governed by the EMA9 trailing stop (which writes
        into trade.stop_loss directly) and strategy exits.
        """
        partial = getattr(trade, "partial_booked", False)
        if trade.direction == "BUY":
            if bar["low"] <= trade.stop_loss:
                reason = "TRAIL_STOP" if partial else (
                    "BREAKEVEN_STOP" if getattr(trade, "breakeven_moved", False) and
                    trade.stop_loss >= trade.entry_price else "STOP_LOSS"
                )
                return True, trade.stop_loss, reason
            if not partial:
                if bar["high"] >= trade.target_2:
                    return True, trade.target_2, "TARGET_2"
                # T1 is handled separately as a partial-book trigger, not a
                # full exit; if we got here without partial it means we have
                # no partial config — fall back to full exit.
                if bar["high"] >= trade.target_1:
                    return True, trade.target_1, "TARGET_1_FULL"
        else:
            if bar["high"] >= trade.stop_loss:
                reason = "TRAIL_STOP" if partial else (
                    "BREAKEVEN_STOP" if getattr(trade, "breakeven_moved", False) and
                    trade.stop_loss <= trade.entry_price else "STOP_LOSS"
                )
                return True, trade.stop_loss, reason
            if not partial:
                if bar["low"] <= trade.target_2:
                    return True, trade.target_2, "TARGET_2"
                if bar["low"] <= trade.target_1:
                    return True, trade.target_1, "TARGET_1_FULL"
        return False, 0.0, ""

    def _book_partial(self, trade: BacktestTrade, exit_price: float, reason: str) -> None:
        """
        Realize 50% of the position at exit_price (journal exit #1 / #5).
        Updates trade.partial_pnl, decrements trade.quantity for the remainder,
        and marks the trade so further partial triggers are ignored. The
        remaining quantity continues to be managed by the trailing stop and
        strategy exits.
        """
        original = getattr(trade, "original_qty", trade.quantity)
        partial_qty = int(round(original * self.strategy.cfg.partial_qty_pct))
        if partial_qty <= 0 or partial_qty >= trade.quantity:
            return  # not enough size to split

        # Apply slippage to the partial fill
        slip = exit_price * self.config.slippage_pct / 100
        actual = (exit_price - slip if trade.direction == "BUY"
                  else exit_price + slip)

        if trade.direction == "BUY":
            partial_pnl = (actual - trade.entry_price) * partial_qty
        else:
            partial_pnl = (trade.entry_price - actual) * partial_qty

        # One brokerage leg for the partial close
        partial_pnl -= self.config.brokerage_per_trade

        trade.partial_pnl = round(getattr(trade, "partial_pnl", 0.0) + partial_pnl, 2)
        trade.partial_booked = True
        trade.partial_reason = reason
        trade.quantity -= partial_qty
        logger.info(
            f"[V2] {trade.symbol} {trade.direction} partial booked "
            f"({partial_qty}@{actual:.2f}, pnl ₹{partial_pnl:,.0f}) — reason: {reason}. "
            f"Remaining {trade.quantity}, trailing by EMA9."
        )

    def _close_trade(self, trade: BacktestTrade, exit_price: float, reason: str,
                     exit_date: str, exit_time: str, result: BacktestResult) -> BacktestTrade:
        """
        Finalise a trade with slippage + brokerage and register the outcome
        with the strategy. The total P&L combines any prior partial book
        with the remainder closing here.
        """
        slippage = exit_price * self.config.slippage_pct / 100
        actual_exit = (exit_price - slippage if trade.direction == "BUY"
                       else exit_price + slippage)

        # Remainder P&L (trade.quantity reflects the size still open)
        if trade.direction == "BUY":
            gross_remainder = (actual_exit - trade.entry_price) * trade.quantity
        else:
            gross_remainder = (trade.entry_price - actual_exit) * trade.quantity

        # Brokerage: entry leg + exit leg. Partial booking already paid for
        # its own exit leg in _book_partial, so we only add 2 legs here for
        # the un-partialed case; 1 leg if a partial was booked (entry leg
        # was paid at open, partial exit leg paid in _book_partial, this is
        # the remainder exit leg).
        partialed = getattr(trade, "partial_booked", False)
        legs = 1 + (1 if partialed else 2)  # entry + 1 (with partial) or 2 (no partial)
        # Entry leg always: subtract once. Exit legs depend on partial:
        # If partialed: partial paid its own leg, plus this one for remainder.
        # If not: this exit leg only.
        brokerage = self.config.brokerage_per_trade * (2 if not partialed else 1)
        net_remainder = gross_remainder - brokerage

        total_net = round(getattr(trade, "partial_pnl", 0.0) + net_remainder, 2)
        total_gross = round(getattr(trade, "partial_pnl", 0.0) + brokerage + gross_remainder, 2)

        trade.exit_date = exit_date
        trade.exit_time = exit_time
        trade.exit_price = round(actual_exit, 2)
        reason_suffix = f"+PARTIAL:{trade.partial_reason}" if partialed else ""
        trade.exit_reason = f"{reason}{reason_suffix}"
        trade.pnl_gross = total_gross
        trade.pnl_net = total_net
        original_qty = getattr(trade, "original_qty", trade.quantity)
        trade.pnl_pct = round(
            total_net / (trade.entry_price * original_qty) * 100, 2
        ) if trade.entry_price > 0 and original_qty > 0 else 0.0

        if trade.entry_price > 0 and trade.stop_loss > 0:
            risk = abs(trade.entry_price - trade.stop_loss)
            reward = abs(actual_exit - trade.entry_price)
            trade.rr_actual = round(reward / risk, 2) if risk > 0 else 0

        result.trades.append(trade)

        # Notify strategy so the 2-consecutive-SL kill switch can arm.
        if hasattr(self.strategy, "register_trade_outcome"):
            try:
                from datetime import datetime as _dt
                when = _dt.strptime(f"{exit_date} {exit_time}", "%Y-%m-%d %H:%M")
                self.strategy.register_trade_outcome(
                    symbol=trade.symbol, when=when,
                    exit_reason=reason, pnl=trade.pnl_net,
                )
            except Exception as e:
                logger.debug(f"register_trade_outcome failed: {e}")

        return trade

    # ─────────────────────────────────────────────
    # SUMMARY
    # ─────────────────────────────────────────────

    def _print_summary(self, result: BacktestResult):
        print(f"\n  📊 {result.symbol} — EMA9/20+VWAP+CPR results")
        print(f"     Trades         : {result.total_trades}")
        print(f"     Win Rate       : {result.win_rate:.1f}%")
        print(f"     Net P&L        : ₹{result.total_pnl:,.0f}")
        print(f"     Profit Factor  : {result.profit_factor}")
        print(f"     Max Drawdown   : {result.max_drawdown:.1f}%")
        print(f"     Sharpe Ratio   : {result.sharpe_ratio}")
        print(f"     Max Consec Loss: {result.max_consecutive_losses}")
        if result.trades:
            exits = {}
            for t in result.trades:
                exits[t.exit_reason] = exits.get(t.exit_reason, 0) + 1
            print(f"     Exit breakdown : {exits}")
