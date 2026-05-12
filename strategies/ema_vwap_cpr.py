"""
EMA10/20 + VWAP + CPR Confluence Strategy
Built strictly to the "My Rules" sheet of trading_journal.xlsx.
(Fast EMA period bumped from 9 → 10 on 12 May 2026 per trader decision.)

ENTRY — all 8 must be YES on a CLOSED 5-min bar:
    1. Price above VWAP (long) / below VWAP (short)
    2. EMA fast crossed above/below EMA slow on this just-closed bar
    3. RSI 55–65 (long) / 35–45 (short)
    4. Volume on crossover candle above 20-period average
    5. Candle has strong body (body / range ≥ 50%), not doji/spinning top
    6. CPR bias agrees with trade direction
    7. Time inside 9:45–11:30 IST or 13:30–14:30 IST
    8. VWAP and EMA agree (implied by rules 1 + 2 — no extra check needed)

EXITS — any of:
    a. Hard SL  : entry  ∓ sl_atr_mult × ATR    (1.5×ATR per current sizing)
    b. Target 1 : entry  ± t1_atr_mult × ATR    (2.5×ATR)
    c. Target 2 : entry  ± t2_atr_mult × ATR    (4.0×ATR)
    d. EMA 9 crosses back against direction    → close immediately (Rule #3)
    e. Price prints on wrong side of VWAP      → close immediately (Rule #4)
    f. EOD square-off (15:15) — handled by main engine

KILL SWITCH (journal Rule #1):
    g. 2 consecutive SL hits today → strategy refuses further entries today.

NOT YET BUILT (need executor support for partial fills):
    • Book 50% at T1 then trail remaining 50% with EMA 9 (journal exits 1-2)
    • RSI 75+ / 25- → book 50%   (journal exit 5)
    • RSI divergence → close all (journal exit 6)
    • Move SL to breakeven after 1:1 move (journal exit 8)
    • Range-day kill switch (VWAP crossed 3+ times by 10 AM)
    • Wide-CPR-no-trend kill switch
"""

import logging
from datetime import datetime, time as dt_time, date as dt_date
from typing import Optional, Tuple, List

import pandas as pd

from data.indicators import ema, vwap, atr, cpr, adx, rsi
from strategies.signal_aggregator import TradeSignal
from config.settings import EMA_VWAP_CPR_CONFIG as CFG

logger = logging.getLogger(__name__)


class EMAVWAPCPRStrategy:
    """Faithful implementation of trading_journal.xlsx 'My Rules'."""

    NAME = "EMA_VWAP_CPR_JOURNAL"

    # Entry windows from journal rule #7 (IST)
    ENTRY_WINDOWS: List[Tuple[int, int, int, int]] = [
        (9, 45, 11, 30),    # morning trend window
        (13, 30, 14, 30),   # afternoon trend window
    ]

    def __init__(self, cfg=CFG):
        self.cfg = cfg
        # Dedup: one signal per closed bar per symbol
        self._last_signal_bar: dict = {}
        # Kill switch: track consecutive SL hits per (symbol, date)
        # value: {"date": dt_date, "consecutive_sl": int}
        self._sl_streak: dict = {}
        # Daily-trend data per symbol (loaded once at backtest/live start)
        # value: pd.DataFrame indexed by daily timestamp with OHLCV columns
        self._daily_data: dict = {}

    # ─────────────────────────────────────────────
    # ENTRY EVALUATION
    # ─────────────────────────────────────────────

    def evaluate(
        self,
        symbol: str,
        exchange: str,
        security_id: str,
        df: pd.DataFrame,
        now: Optional[datetime] = None,
    ) -> TradeSignal:
        """Evaluate the 8-rule journal strategy on the latest closed bar."""
        now = now or datetime.now()
        sig = TradeSignal(
            id=f"{symbol}_J_{int(now.timestamp())}",
            symbol=symbol,
            exchange=exchange,
            security_id=security_id,
            timestamp=now,
            timeframe="5min",
            strategy_name=self.NAME,
        )

        # ── Pre-checks ──
        if df.empty or len(df) < self.cfg.min_bars_required:
            sig.notes = f"insufficient bars ({len(df)})"
            return sig

        # Rule #7 — time window
        if not self.in_entry_window(now):
            sig.notes = f"outside entry window ({now.strftime('%H:%M')})"
            return sig

        # Kill switch #1 — 2 consecutive SL hits today
        if self._kill_switch_active(symbol, now):
            sig.notes = "KILL_2SL 2 consecutive SL today"
            return sig

        # Kill switch #2 — range day (VWAP crossed ≥3 times by 10:00)
        if self.is_range_day_kill_active(df, now):
            sig.notes = "KILL_RANGE VWAP crossed 3+ times by 10:00"
            return sig

        ind = self._compute(df)
        if ind is None:
            sig.notes = "indicators not ready"
            return sig

        # Kill switch #3 — no trend (wide CPR + price inside CPR after 10:30)
        if self.is_no_trend_kill_active(df, ind, now):
            sig.notes = "KILL_NOTREND wide CPR + inside CPR after 10:30"
            return sig

        last_bar_ts = df.index[-1]
        if self._last_signal_bar.get(symbol) == last_bar_ts:
            sig.notes = "already evaluated this bar"
            return sig

        # ── Rule #2: true EMA10/20 crossover on this just-closed bar ──
        cross_up   = ind["ema9_prev"] <= ind["ema20_prev"] and ind["ema9"] > ind["ema20"]
        cross_down = ind["ema9_prev"] >= ind["ema20_prev"] and ind["ema9"] < ind["ema20"]
        if not (cross_up or cross_down):
            return sig

        direction = "BUY" if cross_up else "SELL"

        # ── Daily-trend filter: only take trades aligned with daily trend ──
        if self.cfg.require_daily_trend_alignment:
            dt_trend = self.daily_trend(symbol, now)
            if dt_trend == "NEUTRAL":
                sig.notes = f"{direction} XO skipped: daily trend NEUTRAL"
                return sig
            if direction == "BUY" and dt_trend != "BULL":
                sig.notes = f"{direction} XO skipped: daily trend={dt_trend}"
                return sig
            if direction == "SELL" and dt_trend != "BEAR":
                sig.notes = f"{direction} XO skipped: daily trend={dt_trend}"
                return sig

        ok, why = self._check_entry_filters(direction, ind)
        if not ok:
            sig.notes = f"{direction} cross rejected: {why}"
            return sig

        # All 8 rules pass — emit signal
        close = ind["close"]
        atr_val = ind["atr"]
        if direction == "BUY":
            self._fill_long(sig, close, atr_val)
        else:
            self._fill_short(sig, close, atr_val)
        self._last_signal_bar[symbol] = last_bar_ts
        sig.notes = (
            f"{direction} XO  close={close:.2f} EMA10={ind['ema9']:.2f} EMA20={ind['ema20']:.2f} "
            f"VWAP={ind['vwap']:.2f} RSI={ind['rsi']:.1f} body={ind['body_pct']:.0%} "
            f"vol={ind['volume']:.0f}/{ind['volume_ma20']:.0f}"
        )
        logger.info(f"[{self.NAME}] {symbol} {sig.notes}")
        return sig

    def _check_entry_filters(self, direction: str, ind: dict) -> Tuple[bool, str]:
        """Verify journal entry rules 1, 3, 4, 5, 6 on the crossover bar."""
        cpr_upper = max(ind["tc"], ind["bc"])
        cpr_lower = min(ind["tc"], ind["bc"])

        # Rule #1 — VWAP alignment
        if direction == "BUY" and ind["close"] <= ind["vwap"]:
            return False, f"close {ind['close']:.2f} ≤ VWAP {ind['vwap']:.2f}"
        if direction == "SELL" and ind["close"] >= ind["vwap"]:
            return False, f"close {ind['close']:.2f} ≥ VWAP {ind['vwap']:.2f}"

        # Rule #6 — CPR bias
        if direction == "BUY" and ind["close"] <= cpr_upper:
            return False, f"close {ind['close']:.2f} ≤ CPR top {cpr_upper:.2f}"
        if direction == "SELL" and ind["close"] >= cpr_lower:
            return False, f"close {ind['close']:.2f} ≥ CPR bot {cpr_lower:.2f}"

        # Rule #3 — RSI sweet zone
        if direction == "BUY" and not (self.cfg.rsi_long_min <= ind["rsi"] <= self.cfg.rsi_long_max):
            return False, f"RSI {ind['rsi']:.1f} outside {self.cfg.rsi_long_min}-{self.cfg.rsi_long_max}"
        if direction == "SELL" and not (self.cfg.rsi_short_min <= ind["rsi"] <= self.cfg.rsi_short_max):
            return False, f"RSI {ind['rsi']:.1f} outside {self.cfg.rsi_short_min}-{self.cfg.rsi_short_max}"

        # Rule #5 — strong body
        if ind["body_pct"] < self.cfg.min_body_pct:
            return False, f"body {ind['body_pct']:.0%} < {self.cfg.min_body_pct:.0%} (doji)"

        # Rule #4 — volume above 20-period MA
        if ind["volume_ma20"] > 0 and ind["volume"] < ind["volume_ma20"]:
            return False, f"vol {ind['volume']:.0f} < 20MA {ind['volume_ma20']:.0f}"

        return True, "OK"

    def _fill_long(self, sig: TradeSignal, close: float, atr_val: float) -> None:
        sig.signal = "BUY"
        sig.instrument_type = "FUT"
        sig.entry_price = float(close)
        sig.stop_loss = float(close - self.cfg.sl_atr_mult * atr_val)
        sig.target_1 = float(close + self.cfg.t1_atr_mult * atr_val)
        sig.target_2 = float(close + self.cfg.t2_atr_mult * atr_val)
        sig.confidence = 80.0
        sig.signal_sources = ["VWAP+", "EMA10>20 XO", "RSI 55-65", "Vol>20MA",
                              "StrongBody", "Close>CPR"]
        risk = sig.entry_price - sig.stop_loss
        reward = sig.target_1 - sig.entry_price
        sig.risk_reward = round(reward / risk, 2) if risk > 0 else 0.0

    def _fill_short(self, sig: TradeSignal, close: float, atr_val: float) -> None:
        sig.signal = "SELL"
        sig.instrument_type = "FUT"
        sig.entry_price = float(close)
        sig.stop_loss = float(close + self.cfg.sl_atr_mult * atr_val)
        sig.target_1 = float(close - self.cfg.t1_atr_mult * atr_val)
        sig.target_2 = float(close - self.cfg.t2_atr_mult * atr_val)
        sig.confidence = 80.0
        sig.signal_sources = ["VWAP-", "EMA10<20 XO", "RSI 35-45", "Vol>20MA",
                              "StrongBody", "Close<CPR"]
        risk = sig.stop_loss - sig.entry_price
        reward = sig.entry_price - sig.target_1
        sig.risk_reward = round(reward / risk, 2) if risk > 0 else 0.0

    # ─────────────────────────────────────────────
    # EXIT EVALUATION — journal exits 3 & 4 (immediate on cross)
    # ─────────────────────────────────────────────

    def should_exit(self, position, df: pd.DataFrame) -> Tuple[bool, str]:
        """
        Returns (True, reason) if either:
          • EMA 9 has crossed back against the trade (journal exit #3)
          • Close prints on the wrong side of VWAP (journal exit #4)
        Both are immediate — no buffer, no streak. The journal says
        "close immediately" / "close all" on these triggers.
        """
        if df.empty or len(df) < self.cfg.min_bars_required:
            return False, ""

        ind = self._compute(df)
        if ind is None:
            return False, ""

        ema9, ema20, vwap_val, close = (
            ind["ema9"], ind["ema20"], ind["vwap"], ind["close"]
        )

        if position.direction == "BUY":
            if ema9 < ema20:
                return True, f"EMA_CROSS_BACK (EMA10={ema9:.2f}<EMA20={ema20:.2f})"
            if close < vwap_val:
                return True, f"VWAP_CROSS (close={close:.2f}<VWAP={vwap_val:.2f})"
        elif position.direction == "SELL":
            if ema9 > ema20:
                return True, f"EMA_CROSS_BACK (EMA10={ema9:.2f}>EMA20={ema20:.2f})"
            if close > vwap_val:
                return True, f"VWAP_CROSS (close={close:.2f}>VWAP={vwap_val:.2f})"

        return False, ""

    # ─────────────────────────────────────────────
    # POSITION SIZING (lot-aware) — unchanged
    # ─────────────────────────────────────────────

    @staticmethod
    def recommend_lots(
        capital: float, entry_price: float, stop_loss: float,
        lot_size: int, max_risk_pct: float, max_lots: int,
    ) -> int:
        if entry_price <= 0 or stop_loss <= 0 or lot_size <= 0:
            return 0
        sl_distance = abs(entry_price - stop_loss)
        if sl_distance <= 0:
            return 0
        risk_budget = capital * (max_risk_pct / 100.0)
        risk_per_lot = sl_distance * lot_size
        if risk_per_lot <= 0:
            return 0
        lots = int(risk_budget // risk_per_lot)
        # Allow at least 1 lot if budget covers ≥ 50% of one lot's risk
        lots = max(lots, 1) if risk_budget >= risk_per_lot * 0.5 else lots
        return max(0, min(lots, max_lots))

    # ─────────────────────────────────────────────
    # V2 EXITS & KILL SWITCHES — journal exits 5, 6, 8 + kills 2, 3
    # ─────────────────────────────────────────────

    def should_partial_at_rsi_extreme(self, position, df: pd.DataFrame) -> Tuple[bool, str]:
        """
        Journal exit #5 — RSI 75+ (long) or 25- (short) → book 50%.
        Returns (True, reason) when triggered.
        """
        if df.empty or len(df) < self.cfg.min_bars_required:
            return False, ""
        r = rsi(df["close"], self.cfg.rsi_period).iloc[-1]
        if pd.isna(r):
            return False, ""
        if position.direction == "BUY" and r >= self.cfg.rsi_extreme_overbought:
            return True, f"RSI_EXTREME_OB ({r:.1f}≥{self.cfg.rsi_extreme_overbought})"
        if position.direction == "SELL" and r <= self.cfg.rsi_extreme_oversold:
            return True, f"RSI_EXTREME_OS ({r:.1f}≤{self.cfg.rsi_extreme_oversold})"
        return False, ""

    def detect_rsi_divergence(self, position, df: pd.DataFrame) -> Tuple[bool, str]:
        """
        Journal exit #6 — bearish divergence (long): price new HH but RSI lower HH.
        Bullish divergence (short): price new LL but RSI higher LL.
        Returns (True, reason) when triggered.
        """
        lb = self.cfg.divergence_lookback_bars
        if df.empty or len(df) < lb + 5:
            return False, ""
        recent = df.tail(lb)
        r_series = rsi(df["close"], self.cfg.rsi_period).tail(lb)
        if r_series.isna().any():
            return False, ""

        cur_close = float(recent["close"].iloc[-1])
        cur_rsi = float(r_series.iloc[-1])
        prior_closes = recent["close"].iloc[:-1]
        prior_rsis = r_series.iloc[:-1]
        if prior_closes.empty:
            return False, ""

        if position.direction == "BUY":
            # Bearish divergence — price made a new high, RSI didn't
            prior_high = prior_closes.max()
            if cur_close > prior_high:
                prior_high_idx = prior_closes.idxmax()
                prior_high_rsi = float(prior_rsis.loc[prior_high_idx])
                if cur_rsi < prior_high_rsi:
                    return True, (f"BEARISH_DIV close↑ ({prior_high:.2f}→{cur_close:.2f}) "
                                  f"RSI↓ ({prior_high_rsi:.1f}→{cur_rsi:.1f})")
        elif position.direction == "SELL":
            prior_low = prior_closes.min()
            if cur_close < prior_low:
                prior_low_idx = prior_closes.idxmin()
                prior_low_rsi = float(prior_rsis.loc[prior_low_idx])
                if cur_rsi > prior_low_rsi:
                    return True, (f"BULLISH_DIV close↓ ({prior_low:.2f}→{cur_close:.2f}) "
                                  f"RSI↑ ({prior_low_rsi:.1f}→{cur_rsi:.1f})")
        return False, ""

    def is_range_day_kill_active(self, df: pd.DataFrame, now: datetime) -> bool:
        """
        Journal kill #2 — VWAP crossed ≥ N times before 10:00 AM today.
        Returns True only AFTER 10:00 so we have a full morning sample.
        """
        cutoff = dt_time(self.cfg.range_day_cutoff_hour, self.cfg.range_day_cutoff_min)
        if now.time() < cutoff:
            return False
        today = now.date()
        today_bars = df[df.index.date == today]
        if len(today_bars) < 3:
            return False
        morning = today_bars[today_bars.index.time < cutoff]
        if len(morning) < 3:
            return False
        try:
            today_vwap = vwap(today_bars).reindex(morning.index)
        except Exception:
            return False
        diffs = morning["close"] - today_vwap
        # Count sign changes between consecutive bars
        sign_changes = int(((diffs.shift(1) * diffs) < 0).sum())
        return sign_changes >= self.cfg.range_day_vwap_crosses

    def is_no_trend_kill_active(self, df: pd.DataFrame, ind: dict, now: datetime) -> bool:
        """
        Journal kill #3 — After 10:30 AM, if CPR is wide and price is inside CPR,
        the day is range-bound; no new entries.
        """
        cutoff = dt_time(self.cfg.no_trend_cutoff_hour, self.cfg.no_trend_cutoff_min)
        if now.time() < cutoff:
            return False
        upper = max(ind["tc"], ind["bc"])
        lower = min(ind["tc"], ind["bc"])
        mid = (upper + lower) / 2 if upper > lower else upper
        if mid <= 0:
            return False
        width_pct = (upper - lower) / mid * 100
        is_wide = width_pct > self.cfg.wide_cpr_pct_threshold
        is_inside = lower <= ind["close"] <= upper
        return is_wide and is_inside

    def current_ema9(self, df: pd.DataFrame) -> Optional[float]:
        """Helper for callers that need EMA9 for trailing-stop updates."""
        if df.empty:
            return None
        try:
            v = ema(df["close"], self.cfg.ema_fast).iloc[-1]
            return float(v) if not pd.isna(v) else None
        except Exception:
            return None

    # ── Daily-trend filter (uses 1-year daily data fetched at startup) ──

    def set_daily_data(self, symbol: str, daily_df: pd.DataFrame) -> None:
        """Store the daily OHLCV for a symbol (called once at backtest/live start)."""
        self._daily_data[symbol] = daily_df

    def daily_trend(self, symbol: str, asof: datetime) -> str:
        """
        Return 'BULL', 'BEAR', or 'NEUTRAL' for the day strictly BEFORE asof.
        Using days strictly < asof.date() avoids look-ahead bias in backtest.
        """
        daily_df = self._daily_data.get(symbol)
        if daily_df is None or daily_df.empty:
            return "NEUTRAL"
        hist = daily_df[daily_df.index.date < asof.date()]
        if len(hist) < self.cfg.daily_ema_slow + 5:
            return "NEUTRAL"
        try:
            ema_fast = ema(hist["close"], self.cfg.daily_ema_fast).iloc[-1]
            ema_slow = ema(hist["close"], self.cfg.daily_ema_slow).iloc[-1]
            last_close = float(hist["close"].iloc[-1])
        except Exception:
            return "NEUTRAL"
        if pd.isna(ema_fast) or pd.isna(ema_slow):
            return "NEUTRAL"
        if ema_fast > ema_slow and last_close > ema_fast:
            return "BULL"
        if ema_fast < ema_slow and last_close < ema_fast:
            return "BEAR"
        return "NEUTRAL"

    # ─────────────────────────────────────────────
    # KILL SWITCH — journal kill rule #1
    # ─────────────────────────────────────────────

    def register_trade_outcome(self, symbol: str, when: datetime,
                                exit_reason: str, pnl: float) -> None:
        """
        Called by main/backtest after a trade closes. Updates SL streak.
        A 'SL hit' is any closure with exit reason starting with STOP_LOSS.
        Any winning trade or non-SL exit resets the streak.
        """
        today = when.date() if isinstance(when, datetime) else when
        state = self._sl_streak.get(symbol, {"date": today, "consecutive_sl": 0})
        if state["date"] != today:
            state = {"date": today, "consecutive_sl": 0}

        is_sl_hit = exit_reason.startswith("STOP_LOSS") or exit_reason == "SL_HIT"
        if is_sl_hit and pnl <= 0:
            state["consecutive_sl"] += 1
        else:
            state["consecutive_sl"] = 0

        self._sl_streak[symbol] = state
        if state["consecutive_sl"] >= 2:
            logger.warning(
                f"[{self.NAME}] {symbol} kill switch armed — "
                f"{state['consecutive_sl']} consecutive SL hits today"
            )

    def _kill_switch_active(self, symbol: str, now: datetime) -> bool:
        state = self._sl_streak.get(symbol)
        if not state:
            return False
        if state["date"] != now.date():
            return False
        return state["consecutive_sl"] >= 2

    # ─────────────────────────────────────────────
    # STATE-LIFECYCLE HOOKS (called by main / backtest)
    # ─────────────────────────────────────────────

    def clear_exit_state(self, symbol: str, direction: str) -> None:
        """Backward-compat hook — no internal exit state to clear now."""
        return

    def clear_setup_state(self, symbol: str) -> None:
        """Backward-compat hook — no setup state to clear in journal mode."""
        return

    # ─────────────────────────────────────────────
    # HELPERS
    # ─────────────────────────────────────────────

    def in_entry_window(self, now: datetime) -> bool:
        t = now.time()
        for sh, sm, eh, em in self.ENTRY_WINDOWS:
            if dt_time(sh, sm) <= t <= dt_time(eh, em):
                return True
        return False

    def _compute(self, df: pd.DataFrame) -> Optional[dict]:
        """Indicator snapshot from the latest bar. None if not ready."""
        try:
            close_series = df["close"]
            ema9_series = ema(close_series, self.cfg.ema_fast)
            ema20_series = ema(close_series, self.cfg.ema_slow)
            ema9 = ema9_series.iloc[-1]
            ema20 = ema20_series.iloc[-1]
            ema9_prev = ema9_series.iloc[-2] if len(ema9_series) > 1 else ema9
            ema20_prev = ema20_series.iloc[-2] if len(ema20_series) > 1 else ema20
            vwap_val = vwap(df).iloc[-1]
            atr_val = atr(df, self.cfg.atr_period).iloc[-1]
            rsi_val = rsi(close_series, self.cfg.rsi_period).iloc[-1]
            cpr_data = cpr(df)
            last_bar = df.iloc[-1]
            high, low, open_, close = (
                float(last_bar["high"]), float(last_bar["low"]),
                float(last_bar["open"]), float(last_bar["close"]),
            )
            volume = float(last_bar["volume"])
            volume_ma20 = float(df["volume"].rolling(20).mean().iloc[-1])
        except Exception as e:
            logger.warning(f"[{self.NAME}] indicator compute failed: {e}")
            return None

        if pd.isna(ema9) or pd.isna(ema20) or pd.isna(vwap_val) or pd.isna(atr_val):
            return None
        if not cpr_data or "tc" not in cpr_data or "bc" not in cpr_data:
            return None

        rng = high - low
        body_pct = (abs(close - open_) / rng) if rng > 0 else 0.0

        return {
            "ema9": float(ema9),
            "ema20": float(ema20),
            "ema9_prev": float(ema9_prev) if not pd.isna(ema9_prev) else float(ema9),
            "ema20_prev": float(ema20_prev) if not pd.isna(ema20_prev) else float(ema20),
            "vwap": float(vwap_val),
            "atr": float(atr_val),
            "rsi": float(rsi_val) if not pd.isna(rsi_val) else 50.0,
            "tc": float(cpr_data["tc"]),
            "bc": float(cpr_data["bc"]),
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "body_pct": body_pct,
            "volume": volume,
            "volume_ma20": volume_ma20 if not pd.isna(volume_ma20) else 0.0,
        }
