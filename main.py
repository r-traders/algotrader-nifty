"""
Main Trading Engine Orchestrator
Coordinates all modules in the trading loop:
  1. Market hours check
  2. Option chain refresh & analysis
  3. Market data fetch & indicator computation
  4. SMC analysis
  5. Signal aggregation
  6. Risk check + order execution
  7. Position monitoring
  8. EOD square-off + daily report

Run: python main.py
"""

import time
import logging
import signal as sys_signal
import sys
from datetime import datetime, time as dt_time
from typing import Dict, List, Optional
from dotenv import load_dotenv

# Load environment variables (.env file)
load_dotenv()

# ── Module imports ──
from config.settings import (
    INDEX_SYMBOLS, WATCHLIST_EQUITIES, DEFAULT_EXPIRY_TYPE,
    SCALPING_TF, INTRADAY_TF, SWING_TF,
    MARKET_OPEN_TIME, MARKET_CLOSE_TIME,
    AVOID_TRADE_NEAR_CLOSE_MINS, AVOID_TRADE_NEAR_OPEN_MINS,
    OPTION_CHAIN_CONFIG, DAILY_PNL_REPORT_TIME, NOTIFY_DAILY_PNL,
    EMA_VWAP_CPR_CONFIG, ENABLED_STRATEGIES,
    OC_SNAPSHOT_TIMES,
    LOG_LEVEL, LOG_DIR
)
from data.dhan_client import DhanClient
from data.option_chain_analyzer import OptionChainAnalyzer
from data.indicators import candles_to_df, add_all_indicators
from strategies.smart_money import SmartMoneyAnalyzer
from strategies.signal_aggregator import SignalAggregator
from strategies.ema_vwap_cpr import EMAVWAPCPRStrategy
from risk.risk_manager import RiskManager
from execution.order_executor import OrderExecutor, TelegramNotifier

# ── Logging setup ──
import os
os.makedirs(LOG_DIR, exist_ok=True)

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(f"{LOG_DIR}/trading_engine.log"),
    ]
)
logger = logging.getLogger("TradingEngine")

# ── Instrument registry (symbol → security_id mapping) ──
# Update these with actual Dhan security IDs from their instrument CSV
# Security IDs verified from Dhan api-scrip-master.csv (April 2026)
# NIFTY and BANKNIFTY near-month futures — UPDATE AFTER EVERY MONTHLY EXPIRY.
# Current near-month (May 11–26, 2026): MAY 2026 contracts.
# Expiry: Tue 27 May 2026 → switch to JUN (62329 / 62326) on the morning of 27 May.
# After June expiry switch to JUL (61093 / 61088). Run find_futures_ids.py to refresh.
INSTRUMENT_REGISTRY = {
    "NIFTY":     {
        "security_id": "66071",   # NIFTY-MAY2026-FUT — rolls 27 May → 62329 (JUN)
        "exchange":    "NSE_FNO",
        "instrument":  "FUTIDX",
        "idx_id":      "13",      # NSE_IDX ID for LTP/option chain
        "lot_size":    65,        # current SEBI lot size
        "base_price":  22000,
    },
    "BANKNIFTY": {
        "security_id": "66068",   # BANKNIFTY-MAY2026-FUT — rolls 27 May → 62326 (JUN)
        "exchange":    "NSE_FNO",
        "instrument":  "FUTIDX",
        "idx_id":      "25",      # NSE_IDX ID for LTP/option chain
        "lot_size":    30,        # current SEBI lot size
        "base_price":  47000,
    },
}

# Symbols traded by the EMA9/20 + VWAP + CPR confluence strategy
EMA_STRATEGY_SYMBOLS = ["NIFTY", "BANKNIFTY"]


class TradingEngine:
    """
    Main engine that runs the full trading loop.
    Designed to be run during market hours (9:15 AM – 3:30 PM IST).
    """

    def __init__(self, capital: float = 500_000.0):
        logger.info("=" * 60)
        logger.info("🚀 Initializing Automated Trading System")
        logger.info("=" * 60)

        self.dhan = DhanClient()
        self.oc_analyzer = OptionChainAnalyzer()
        self.smc_analyzer = SmartMoneyAnalyzer()
        self.signal_aggregator = SignalAggregator()
        self.risk_manager = RiskManager(total_capital=capital)
        self.executor = OrderExecutor(self.dhan, self.risk_manager)
        self.notifier = TelegramNotifier()
        self.ema_strategy = EMAVWAPCPRStrategy()

        self._running = False
        self._last_oc_refresh: Dict[str, datetime] = {}
        self._daily_report_sent = False
        self._pnl_report_sent = False
        # OC snapshot tracking — set of (date, slot, symbol) tuples already
        # snapshotted today; reset implicitly when the date in the tuple changes.
        self._oc_snaps_taken: set = set()

        # Setup graceful shutdown
        sys_signal.signal(sys_signal.SIGINT, self._shutdown)
        sys_signal.signal(sys_signal.SIGTERM, self._shutdown)

        logger.info(f"✅ Engine initialized | Capital: ₹{capital:,.0f}")

        # ── Startup API Validation ──────────────────────────────────────
        logger.info("🔍 Validating Dhan API connection...")
        ok, info = self.dhan.validate_connection()
        if ok:
            avail = info.get("availabelBalance", info.get("availableBalance", "N/A"))
            logger.info(f"✅ Dhan API connected | Available margin: ₹{avail}")
            self.notifier.send(
                f"🟢 <b>API Connection OK</b>\n"
                f"💰 Available Margin: ₹{avail}\n"
                f"📋 Mode: {'PAPER' if self.dhan.paper_trading else '🔴 LIVE'}"
            )
        else:
            logger.error(f"❌ Dhan API validation FAILED: {info}")
            logger.error("⚠️  Run update_token.py to refresh your token before trading")
            self.notifier.send(
                f"🔴 <b>API Validation FAILED</b>\n"
                f"❌ {info}\n"
                f"🔧 Run: python3 update_token.py"
            )
            # Don't abort — allow paper trading / backtest to continue
            if not self.dhan.paper_trading:
                logger.warning("Live trading will be blocked until token is refreshed")

    # ─────────────────────────────────────────────
    # MAIN LOOP
    # ─────────────────────────────────────────────

    def run(self):
        """Start the main trading loop."""
        self._running = True
        logger.info("▶ Trading engine started")
        self.notifier.send("🟢 <b>Trading Engine Started</b>\nMarket: NSE | Mode: " +
                           ("PAPER" if self.dhan.paper_trading else "LIVE"))

        while self._running:
            try:
                now = datetime.now()
                self._check_market_hours(now)

                if self._is_trading_time(now):
                    self._trading_cycle(now)
                elif self._is_eod_time(now):
                    self._eod_routine(now)
                else:
                    logger.debug(f"Outside trading hours: {now.strftime('%H:%M')}")
                    time.sleep(60)

            except KeyboardInterrupt:
                break
            except Exception as e:
                logger.error(f"Engine loop error: {e}", exc_info=True)
                self.notifier.send(f"⚠️ <b>Engine Error</b>\n{str(e)[:200]}")
                time.sleep(30)

        logger.info("⏹ Trading engine stopped")

    def _trading_cycle(self, now: datetime):
        """One full scan cycle: data → indicators → signals → execution."""
        logger.info(f"── Trading Cycle: {now.strftime('%H:%M:%S')} ──")

        price_feed = {}

        # ── Index futures: EMA9/20 + VWAP + CPR confluence strategy ──
        # (Option chain still refreshed for dashboard/IV cache, but does
        # not drive trades for these symbols.)
        if ENABLED_STRATEGIES.get("ema_vwap_cpr_confluence"):
            for symbol in EMA_STRATEGY_SYMBOLS:
                self._refresh_option_chain(symbol)            # for dashboard only
                self._monitor_index_strategy_exits(symbol)    # check EMA/VWAP exits first
                self._process_index_strategy(symbol, price_feed, now)

        # ── Equity / ETF scan ──
        for symbol in WATCHLIST_EQUITIES[:5]:  # Top 5 equities
            self._process_equity_signal(symbol, price_feed)

        # ── Monitor open positions (hard SL / target hits) ──
        if price_feed:
            exited = self.executor.monitor_positions(price_feed)
            if exited:
                logger.info(f"Positions exited this cycle: {exited}")
                # Clear EMA strategy exit-confirmation streaks for any
                # index-strategy position that just closed via SL/target.
                for pos_id in exited:
                    pos = self.risk_manager.positions.get(pos_id)
                    if pos and pos.symbol in EMA_STRATEGY_SYMBOLS:
                        self.ema_strategy.clear_exit_state(pos.symbol, pos.direction)
                        self.ema_strategy.clear_setup_state(pos.symbol)

        # Wait for next cycle
        time.sleep(OPTION_CHAIN_CONFIG.refresh_interval_secs)

    # ─────────────────────────────────────────────
    # OPTION CHAIN FLOW
    # ─────────────────────────────────────────────

    def _refresh_option_chain(self, symbol: str):
        """Fetch and analyze option chain. Returns OptionChainSignal or None."""
        now = datetime.now()
        last_refresh = self._last_oc_refresh.get(symbol)
        if last_refresh:
            elapsed = (now - last_refresh).total_seconds()
            if elapsed < OPTION_CHAIN_CONFIG.refresh_interval_secs:
                return None

        # Get nearest expiry
        expiry = self._get_nearest_expiry(symbol)
        if not expiry:
            return None

        raw_chain = self.dhan.get_option_chain(symbol, expiry)
        if not raw_chain:
            return None

        self._last_oc_refresh[symbol] = now
        result = self.oc_analyzer.analyze(raw_chain, symbol, expiry)

        # Write IV cache so dashboard shows live IVP/IVR
        try:
            iv_cache_path = os.path.join(LOG_DIR, "iv_cache.json")
            self.oc_analyzer.write_iv_cache(iv_cache_path)
        except Exception as e:
            logger.debug(f"IV cache write failed: {e}")

        # Take scheduled OC snapshot if we're inside any of the snapshot windows
        try:
            self._maybe_snapshot_oc(symbol, result, now)
        except Exception as e:
            logger.warning(f"OC snapshot failed for {symbol}: {e}")

        return result

    def _maybe_snapshot_oc(self, symbol: str, oc_signal, now: datetime) -> None:
        """
        Persist a row to logs/oc_snapshots_<date>.json if `now` falls within
        the +60-second window of any scheduled OC_SNAPSHOT_TIMES entry and
        we haven't taken that (date, slot, symbol) snapshot yet today.
        """
        if oc_signal is None:
            return
        import json as _json
        from datetime import time as _dt_time

        today = now.date()
        for slot_time, slot_name in OC_SNAPSHOT_TIMES:
            hh, mm = map(int, slot_time.split(":"))
            target_min = hh * 60 + mm
            now_min = now.hour * 60 + now.minute
            # Snapshot fires in a 2-minute window starting at the scheduled
            # minute (engine cycle is 60s so we want some slack).
            if not (target_min <= now_min <= target_min + 1):
                continue

            key = (today.isoformat(), slot_name, symbol)
            if key in self._oc_snaps_taken:
                continue

            entry = {
                "slot":         slot_name,
                "scheduled":    slot_time,
                "timestamp":    now.strftime("%Y-%m-%d %H:%M:%S"),
                "symbol":       symbol,
                "spot_price":   round(getattr(oc_signal, "spot_price", 0) or 0, 2),
                "max_pain":     round(getattr(oc_signal, "max_pain", 0) or 0, 2),
                "max_pain_dist_pct": round(getattr(oc_signal, "max_pain_distance_pct", 0) or 0, 2),
                "pcr_oi":       round(getattr(oc_signal, "pcr_oi", 0) or 0, 3),
                "pcr_volume":   round(getattr(oc_signal, "pcr_volume", 0) or 0, 3),
                "pcr_bias":     getattr(oc_signal, "pcr_bias", "NEUTRAL"),
                "call_wall":    round(getattr(oc_signal, "call_wall", 0) or 0, 2),
                "put_wall":     round(getattr(oc_signal, "put_wall", 0) or 0, 2),
                "atm_iv":       round(getattr(oc_signal, "atm_iv_avg", 0) or 0, 2),
                "signal":       getattr(oc_signal, "signal", ""),
            }

            path = os.path.join(LOG_DIR, f"oc_snapshots_{today.isoformat()}.json")
            existing = {"date": today.isoformat(), "snapshots": []}
            if os.path.exists(path):
                try:
                    with open(path) as f:
                        existing = _json.load(f)
                except Exception:
                    pass
            existing.setdefault("snapshots", []).append(entry)
            with open(path, "w") as f:
                _json.dump(existing, f, indent=2)

            self._oc_snaps_taken.add(key)
            logger.info(
                f"📸 OC snapshot saved | {slot_time} {slot_name} | {symbol} "
                f"MP={entry['max_pain']} PCR={entry['pcr_oi']} "
                f"CallWall={entry['call_wall']} PutWall={entry['put_wall']}"
            )
            # Telegram nudge
            try:
                self.notifier.send(
                    f"📸 <b>OC Snapshot — {slot_time} {slot_name}</b>\n"
                    f"<b>{symbol}</b>\n"
                    f"Max Pain: <code>{entry['max_pain']}</code>\n"
                    f"PCR (OI): <code>{entry['pcr_oi']}</code>  ({entry['pcr_bias']})\n"
                    f"Call Wall: <code>{entry['call_wall']}</code>\n"
                    f"Put Wall: <code>{entry['put_wall']}</code>\n"
                    f"ATM IV: <code>{entry['atm_iv']}%</code>"
                )
            except Exception:
                pass
            break  # one slot per call

    def _process_options_signal(self, symbol: str, oc_signal, price_feed: Dict):
        """Process an option chain signal and potentially trade it."""
        instrument_info = INSTRUMENT_REGISTRY.get(symbol, {})
        security_id = instrument_info.get("security_id", "")
        exchange = instrument_info.get("exchange", "NSE_FNO")

        # Fetch intraday candles
        df = self._fetch_candles(security_id, exchange, "FUTIDX", interval="5")
        if df.empty:
            return

        df = add_all_indicators(df)

        # Update price feed
        if not df.empty:
            price_feed[security_id] = df["close"].iloc[-1]

        # SMC analysis on index futures
        smc_signal = self.smc_analyzer.analyze(df, symbol)

        # Aggregate signals
        final_signal = self.signal_aggregator.aggregate(
            symbol=symbol,
            exchange=exchange,
            security_id=security_id,
            df=df,
            oc_signal=oc_signal,
            smc_signal=smc_signal,
            timeframe=INTRADAY_TF,
        )

        if final_signal.is_valid:
            logger.info(f"🎯 Valid signal generated: {symbol} {final_signal.signal} "
                        f"Conf={final_signal.confidence:.0f}%")
            success, msg = self.executor.execute_signal(final_signal)
            if success:
                logger.info(f"✅ Trade executed: {msg}")
            else:
                logger.info(f"⛔ Trade not executed: {msg}")

    # ─────────────────────────────────────────────
    # EQUITY FLOW
    # ─────────────────────────────────────────────

    def _process_equity_signal(self, symbol: str, price_feed: Dict):
        """Process an equity signal."""
        instrument_info = INSTRUMENT_REGISTRY.get(symbol, {})
        security_id = instrument_info.get("security_id", "")
        exchange = instrument_info.get("exchange", "NSE_EQ")

        if not security_id:
            return

        df = self._fetch_candles(security_id, exchange, "EQUITY", interval="5")
        if df.empty:
            return

        df = add_all_indicators(df)
        if not df.empty:
            price_feed[security_id] = df["close"].iloc[-1]

        smc_signal = self.smc_analyzer.analyze(df, symbol)
        final_signal = self.signal_aggregator.aggregate(
            symbol=symbol,
            exchange=exchange,
            security_id=security_id,
            df=df,
            oc_signal=None,
            smc_signal=smc_signal,
            timeframe=INTRADAY_TF,
        )

        if final_signal.is_valid:
            success, msg = self.executor.execute_signal(final_signal)
            logger.info(f"{'✅' if success else '⛔'} {symbol} equity trade: {msg}")

    # ─────────────────────────────────────────────
    # INDEX FUTURES — EMA + VWAP + CPR CONFLUENCE
    # ─────────────────────────────────────────────

    def _process_index_strategy(self, symbol: str, price_feed: Dict, now: datetime):
        """Run the EMA/VWAP/CPR strategy for NIFTY/BANKNIFTY index futures."""
        info = INSTRUMENT_REGISTRY.get(symbol)
        if not info or not info.get("security_id"):
            logger.warning(f"[{symbol}] No instrument registry entry; skipping")
            return

        df = self._fetch_candles(
            info["security_id"], info["exchange"], info["instrument"], interval="5"
        )
        if df.empty:
            logger.debug(f"[{symbol}] no candles returned")
            return

        # Update price feed for hard SL/target monitoring
        price_feed[info["security_id"]] = float(df["close"].iloc[-1])

        signal = self.ema_strategy.evaluate(
            symbol=symbol,
            exchange=info["exchange"],
            security_id=info["security_id"],
            df=df,
            now=now,
        )

        if signal.signal not in ("BUY", "SELL"):
            return

        # Skip if there's already an OPEN position on this symbol+direction
        for p in self.risk_manager.positions.values():
            if p.symbol == symbol and p.status == "OPEN" and p.direction == signal.signal:
                logger.info(f"[{symbol}] Already have OPEN {signal.signal} position — skip new entry")
                return

        # Lot-aware position sizing
        lots = self.ema_strategy.recommend_lots(
            capital=self.risk_manager.total_capital,
            entry_price=signal.entry_price,
            stop_loss=signal.stop_loss,
            lot_size=info["lot_size"],
            max_risk_pct=EMA_VWAP_CPR_CONFIG.max_risk_per_trade_pct,
            max_lots=EMA_VWAP_CPR_CONFIG.max_lots_per_trade,
        )
        if lots <= 0:
            logger.info(f"[{symbol}] Risk-based sizing returned 0 lots — skip trade")
            return

        signal.quantity = lots * info["lot_size"]
        logger.info(
            f"[{symbol}] {signal.signal} signal | entry={signal.entry_price:.2f} "
            f"SL={signal.stop_loss:.2f} T1={signal.target_1:.2f} R:R={signal.risk_reward} "
            f"| {lots} lot(s) × {info['lot_size']} = qty {signal.quantity}"
        )

        success, msg = self.executor.execute_signal(signal)
        logger.info(f"{'✅' if success else '⛔'} {symbol} EMA-VWAP-CPR: {msg}")
        if success:
            # Fresh exit-confirmation streak for the new position
            self.ema_strategy.clear_exit_state(symbol, signal.signal)

    def _monitor_index_strategy_exits(self, symbol: str):
        """Force-exit open positions if strategy-specific exits trigger."""
        open_positions = [
            p for p in self.risk_manager.positions.values()
            if p.symbol == symbol and p.status == "OPEN"
        ]
        if not open_positions:
            return

        info = INSTRUMENT_REGISTRY.get(symbol)
        if not info:
            return

        df = self._fetch_candles(
            info["security_id"], info["exchange"], info["instrument"], interval="5"
        )
        if df.empty:
            return

        for pos in open_positions:
            should_exit, reason = self.ema_strategy.should_exit(pos, df)
            if should_exit:
                exit_price = float(df["close"].iloc[-1])
                logger.info(f"[{symbol}] Strategy exit fired: {reason} @ {exit_price:.2f}")
                self.executor.exit_position(pos, exit_price, f"STRATEGY_EXIT: {reason}")
                # Clear streak + setup state so the next crossover starts
                # a fresh retest setup from IDLE.
                self.ema_strategy.clear_exit_state(symbol, pos.direction)
                self.ema_strategy.clear_setup_state(symbol)

    # ─────────────────────────────────────────────
    # DATA HELPERS
    # ─────────────────────────────────────────────

    def _fetch_candles(self, security_id: str, exchange: str,
                       instrument_type: str, interval: str = "5"):
        """Fetch OHLCV candles and return as DataFrame."""
        from datetime import date, timedelta
        today = date.today().strftime("%Y-%m-%d")
        from_date = (date.today() - timedelta(days=5)).strftime("%Y-%m-%d")

        candles = self.dhan.get_historical_data(
            security_id=security_id,
            exchange_segment=exchange,
            instrument_type=instrument_type,
            from_date=from_date,
            to_date=today,
            interval=interval,
        )
        return candles_to_df(candles)

    def _get_nearest_expiry(self, symbol: str) -> Optional[str]:
        """Get nearest weekly/monthly expiry date for a symbol."""
        from datetime import date, timedelta

        try:
            data = self.dhan.get_option_chain_all_expiries(symbol)
            # data["data"] can be a list (success) or a dict (error response)
            raw = data.get("data", [])
            if isinstance(raw, list) and raw:
                # Filter to only valid date strings
                expiries = [e for e in raw if isinstance(e, str) and len(e) == 10]
                if expiries:
                    return expiries[0]
            if data.get("status") == "failed":
                logger.warning(f"Expiry list API failed for {symbol}: {data}")
        except Exception as e:
            logger.warning(f"_get_nearest_expiry error for {symbol}: {e}")

        # Fallback: nearest upcoming Thursday (weekly expiry)
        today = date.today()
        days_to_thursday = (3 - today.weekday()) % 7
        if days_to_thursday == 0:
            days_to_thursday = 7
        thursday = today + timedelta(days=days_to_thursday)
        logger.info(f"Using fallback expiry for {symbol}: {thursday}")
        return thursday.strftime("%Y-%m-%d")

    # ─────────────────────────────────────────────
    # MARKET HOURS
    # ─────────────────────────────────────────────

    def _is_trading_time(self, now: datetime) -> bool:
        """Check if it's within active trading hours."""
        t = now.time()
        open_h, open_m = map(int, MARKET_OPEN_TIME.split(":"))
        close_h, close_m = map(int, MARKET_CLOSE_TIME.split(":"))
        open_time = dt_time(open_h, open_m + AVOID_TRADE_NEAR_OPEN_MINS)
        close_time = dt_time(close_h, close_m - AVOID_TRADE_NEAR_CLOSE_MINS)
        return open_time <= t <= close_time and now.weekday() < 5  # Mon–Fri

    def _is_eod_time(self, now: datetime) -> bool:
        close_h, close_m = map(int, MARKET_CLOSE_TIME.split(":"))
        eod_start = dt_time(close_h, close_m - AVOID_TRADE_NEAR_CLOSE_MINS)
        eod_end = dt_time(close_h, close_m + 5)
        return eod_start <= now.time() <= eod_end and now.weekday() < 5

    def _check_market_hours(self, now: datetime):
        """Send morning start notification."""
        open_h, open_m = map(int, MARKET_OPEN_TIME.split(":"))
        if (now.time().hour == open_h and
                now.time().minute == open_m and
                not self._daily_report_sent):
            self.notifier.send(
                f"🔔 <b>Market Open</b> — {now.strftime('%d %b %Y')}\n"
                f"Trading engine active | Capital: ₹{self.risk_manager.total_capital:,.0f}"
            )
            self._daily_report_sent = True
            self._pnl_report_sent = False

    def _eod_routine(self, now: datetime):
        """End-of-day: square off all positions and send summary."""
        if not self._pnl_report_sent:
            logger.info("📊 EOD: Squaring off all open positions...")
            # Build last known price feed from positions
            price_feed = {
                p.security_id: p.current_price
                for p in self.risk_manager.positions.values()
                if p.status == "OPEN"
            }
            self.executor.square_off_all(price_feed)

            # Send daily summary
            summary = self.risk_manager.get_daily_summary()
            logger.info(f"Daily P&L: ₹{summary['daily_pnl']:,.0f} | "
                        f"Trades: {summary['total_trades']} | "
                        f"Win Rate: {summary['win_rate']:.1f}%")

            if NOTIFY_DAILY_PNL:
                self.executor.notifier.daily_summary(summary)

            self._pnl_report_sent = True

        time.sleep(120)  # Wait 2 min then continue

    # ─────────────────────────────────────────────
    # SHUTDOWN
    # ─────────────────────────────────────────────

    def _shutdown(self, signum, frame):
        logger.info("🛑 Shutdown signal received. Stopping engine...")
        self._running = False
        # Emergency square-off
        price_feed = {
            p.security_id: p.current_price
            for p in self.risk_manager.positions.values()
            if p.status == "OPEN"
        }
        if price_feed:
            self.executor.square_off_all(price_feed)
        self.notifier.send("🔴 <b>Trading Engine Stopped</b>")
        sys.exit(0)


# ─────────────────────────────────────────────
# ENTRY POINT
# ─────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Automated Trading System — Dhan + NSE")
    parser.add_argument("--capital", type=float, default=500_000.0,
                        help="Total trading capital in INR (default: 5,00,000)")
    parser.add_argument("--live", action="store_true",
                        help="Enable live trading (default is paper trading)")
    args = parser.parse_args()

    if args.live:
        import os
        os.environ["PAPER_TRADING"] = "false"
        print("\n⚠️  WARNING: LIVE TRADING MODE ENABLED ⚠️")
        print("   Real orders will be placed with your Dhan account!")
        confirm = input("   Type 'CONFIRM' to proceed: ")
        if confirm != "CONFIRM":
            print("Aborted.")
            sys.exit(0)

    engine = TradingEngine(capital=args.capital)
    engine.run()
