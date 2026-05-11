"""
Order Executor
Bridges strategy signals → Dhan API order placement.
Handles:
  - Entry order placement (LIMIT / MARKET)
  - Stop-loss order (SL-M)
  - Target order (LIMIT)
  - Bracket-style management
  - Order status tracking
  - Telegram notifications
"""

import logging
import time
import asyncio
import requests
from typing import Optional, Dict, Tuple, List
from datetime import datetime

from data.dhan_client import DhanClient
from risk.risk_manager import RiskManager, Position
from strategies.signal_aggregator import TradeSignal
from config.settings import (
    TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID,
    NOTIFY_ON_TRADE, NOTIFY_ON_SIGNAL, NOTIFY_ON_ERROR,
    PAPER_TRADING
)

logger = logging.getLogger(__name__)


class TelegramNotifier:
    """Sends trade alerts via Telegram bot."""

    def __init__(self, token: str = TELEGRAM_BOT_TOKEN, chat_id: str = TELEGRAM_CHAT_ID):
        self.token = token
        self.chat_id = chat_id
        self.enabled = bool(token and chat_id)

    def send(self, message: str):
        if not self.enabled:
            return
        try:
            url = f"https://api.telegram.org/bot{self.token}/sendMessage"
            requests.post(url, json={
                "chat_id": self.chat_id,
                "text": message,
                "parse_mode": "HTML",
            }, timeout=5)
        except Exception as e:
            logger.error(f"Telegram notification failed: {e}")

    def trade_alert(self, action: str, signal: TradeSignal, order_id: str = ""):
        msg = (
            f"<b>{'✅' if action == 'ENTRY' else '🔴' if 'SL' in action else '🎯'} "
            f"{action} | {'PAPER' if PAPER_TRADING else 'LIVE'}</b>\n"
            f"📊 <b>{signal.symbol}</b> {signal.instrument_type} "
            f"{'CALL' if signal.instrument_type == 'CE' else 'PUT' if signal.instrument_type == 'PE' else ''}"
            f"{f' {signal.strike:.0f}' if signal.strike else ''}\n"
            f"💰 Entry: <code>{signal.entry_price:.2f}</code>\n"
            f"🛑 SL: <code>{signal.stop_loss:.2f}</code>\n"
            f"🎯 T1: <code>{signal.target_1:.2f}</code>\n"
            f"📈 Conf: {signal.confidence:.0f}% | R:R {signal.risk_reward}\n"
            f"🔖 Strategies: {signal.notes}\n"
            f"🆔 {order_id if order_id else 'N/A'}\n"
            f"⏰ {datetime.now().strftime('%H:%M:%S')}"
        )
        self.send(msg)

    def pnl_alert(self, pos: Position, exit_reason: str):
        emoji = "✅" if pos.pnl >= 0 else "❌"
        msg = (
            f"<b>{emoji} POSITION CLOSED | {exit_reason}</b>\n"
            f"📊 {pos.symbol} {pos.instrument_type}\n"
            f"📥 Entry: <code>{pos.entry_price:.2f}</code> → "
            f"📤 Exit: <code>{pos.exit_price:.2f}</code>\n"
            f"💵 P&L: <code>₹{pos.pnl:,.0f}</code> ({pos.pnl_pct:.1f}%)\n"
            f"⏰ {datetime.now().strftime('%H:%M:%S')}"
        )
        self.send(msg)

    def daily_summary(self, summary: Dict):
        pnl = summary.get("daily_pnl", 0)
        emoji = "✅" if pnl >= 0 else "❌"
        msg = (
            f"<b>{emoji} Daily Summary — {summary.get('date')}</b>\n"
            f"💰 Net P&L: <code>₹{pnl:,.0f}</code> ({summary.get('daily_pnl_pct', 0):.2f}%)\n"
            f"📊 Trades: {summary.get('total_trades', 0)} | "
            f"W: {summary.get('winners', 0)} L: {summary.get('losers', 0)}\n"
            f"🎯 Win Rate: {summary.get('win_rate', 0):.1f}%\n"
            f"💼 Avail Capital: ₹{summary.get('available_capital', 0):,.0f}"
        )
        self.send(msg)


class OrderExecutor:
    """
    Handles full trade lifecycle:
    1. Pre-trade validation (via RiskManager)
    2. Entry order → Dhan API
    3. SL & target order placement
    4. Position monitoring loop
    5. Exit order on SL/target/EOD
    6. Telegram alerts
    """

    def __init__(self, dhan: DhanClient, risk: RiskManager):
        self.dhan = dhan
        self.risk = risk
        self.notifier = TelegramNotifier()
        self._active_orders: Dict[str, Dict] = {}  # order_id → metadata

    # ─────────────────────────────────────────────
    # EXECUTE SIGNAL
    # ─────────────────────────────────────────────

    def execute_signal(self, signal: TradeSignal) -> Tuple[bool, str]:
        """
        Main entry point: validates and executes a trade signal.
        Returns (success: bool, message: str)

        Flow:
          1. Signal quality check
          2. Risk pre-check
          3. API token validation  ← NEW: aborts if token expired
          4. Margin sufficiency check ← NEW: aborts if insufficient funds
          5. Place entry order
          6. Place SL order
          7. Telegram alert
        """
        # 1. Validate signal quality
        if not signal.is_valid:
            logger.debug(f"Signal not valid for execution: {signal.symbol} conf={signal.confidence:.0f}%")
            return False, "SIGNAL_NOT_VALID"

        # 2. Risk pre-check
        allowed, reason = self.risk.can_trade(signal)
        if not allowed:
            logger.info(f"Trade blocked by risk manager: {reason}")
            return False, reason

        # 3. ── PRE-TRADE API VALIDATION ──────────────────────────────────
        #    Skip this check in paper trading (no real API call needed)
        if not PAPER_TRADING:
            ok, val_info = self.dhan.validate_connection()
            if not ok:
                err_msg = f"⛔ ORDER BLOCKED — API validation failed: {val_info}"
                logger.error(err_msg)
                if NOTIFY_ON_ERROR:
                    self.notifier.send(
                        f"<b>⛔ ORDER BLOCKED</b>\n"
                        f"📊 Symbol: {signal.symbol}\n"
                        f"❌ Reason: {val_info}\n"
                        f"🔧 Action: Run update_token.py and restart engine\n"
                        f"⏰ {datetime.now().strftime('%H:%M:%S')}"
                    )
                return False, f"API_VALIDATION_FAILED: {val_info}"

            # 4. Margin check — ensure enough funds for at least 1 lot
            available_margin = float(
                val_info.get("availabelBalance",
                val_info.get("availableBalance", 0)) or 0
            )
            required_margin  = signal.entry_price * signal.quantity * 0.20   # ~20% NRML margin approx
            if available_margin > 0 and available_margin < required_margin:
                err_msg = (f"Insufficient margin: need ≈₹{required_margin:,.0f}, "
                           f"have ₹{available_margin:,.0f}")
                logger.warning(f"[PreTrade] ⚠️ {err_msg}")
                if NOTIFY_ON_ERROR:
                    self.notifier.send(
                        f"<b>⚠️ INSUFFICIENT MARGIN</b>\n"
                        f"📊 {signal.symbol} | Need: ₹{required_margin:,.0f} | "
                        f"Available: ₹{available_margin:,.0f}\n"
                        f"⏰ {datetime.now().strftime('%H:%M:%S')}"
                    )
                return False, err_msg

        # 5. Calculate quantity — honor pre-set signal.quantity (lot-aware
        #    sizing from strategy) if the strategy already chose lots,
        #    otherwise fall back to RiskManager's % sizing.
        if signal.quantity and signal.quantity > 0:
            qty = signal.quantity
        else:
            qty = self.risk.calculate_position_size(signal)
            signal.quantity = qty

        # 6. Place entry order
        logger.info(f"🚀 Executing signal: {signal.symbol} {signal.signal} {signal.instrument_type} "
                    f"Qty={qty} @ {signal.entry_price:.2f} | LIVE={'YES' if not PAPER_TRADING else 'NO (PAPER)'}")

        order_result = self._place_entry(signal, qty)
        if not order_result:
            if not PAPER_TRADING and NOTIFY_ON_ERROR:
                self.notifier.send(
                    f"<b>❌ ENTRY ORDER FAILED</b>\n"
                    f"📊 {signal.symbol} {signal.signal} {signal.instrument_type}\n"
                    f"💰 Price: {signal.entry_price:.2f} | Qty: {qty}\n"
                    f"⏰ {datetime.now().strftime('%H:%M:%S')}"
                )
            return False, "ENTRY_ORDER_FAILED"

        order_id = order_result.get("orderId", "")

        # 7. Register position
        position = self.risk.open_position(signal, order_id)
        signal.executed = True
        signal.execution_time = datetime.now()

        # 8. Place SL order
        self._place_sl_order(signal, position)

        # 9. Notify
        if NOTIFY_ON_TRADE:
            self.notifier.trade_alert("ENTRY", signal, order_id)

        return True, order_id

    # ─────────────────────────────────────────────
    # ORDER PLACEMENT
    # ─────────────────────────────────────────────

    def _place_entry(self, signal: TradeSignal, qty: int) -> Optional[Dict]:
        """Place entry order — LIMIT slightly adjusted for fills, or MARKET."""
        exchange_segment = self._get_exchange_segment(signal)
        product_type = self._get_product_type(signal)

        # Use MARKET for options/futures scalping, LIMIT for equity
        if signal.instrument_type in ("CE", "PE", "FUT"):
            order_type = "MARKET"
            price = 0.0
        else:
            order_type = "LIMIT"
            # Limit entry: 0.1% above for BUY, 0.1% below for SELL (ensure fill)
            adj = 1.001 if signal.signal == "BUY" else 0.999
            price = round(signal.entry_price * adj, 2)

        result = self.dhan.place_order(
            security_id=signal.security_id,
            exchange_segment=exchange_segment,
            transaction_type=signal.signal,
            quantity=qty,
            order_type=order_type,
            product_type=product_type,
            price=price,
            tag=signal.id[:20],
        )
        return result if result else None

    def _place_sl_order(self, signal: TradeSignal, position: Position) -> Optional[str]:
        """Place stop-loss order for open position."""
        exchange_segment = self._get_exchange_segment(signal)
        product_type = self._get_product_type(signal)
        sl_exit_side = "SELL" if signal.signal == "BUY" else "BUY"

        result = self.dhan.place_order(
            security_id=signal.security_id,
            exchange_segment=exchange_segment,
            transaction_type=sl_exit_side,
            quantity=position.quantity,
            order_type="STOP_LOSS_MARKET",
            product_type=product_type,
            trigger_price=round(signal.stop_loss, 2),
            tag=f"SL_{signal.id[:15]}",
        )
        if result:
            sl_order_id = result.get("orderId", "")
            self._active_orders[position.id] = {
                "sl_order_id": sl_order_id,
                "target_order_id": "",
            }
            return sl_order_id
        return None

    def exit_position(self, position: Position, exit_price: float, reason: str) -> bool:
        """Exit a position immediately at market price."""
        exchange_segment = self._get_exchange_segment_from_pos(position)
        product_type = "INTRADAY" if position.instrument_type in ("CE", "PE", "FUT") else "CNC"
        exit_side = "SELL" if position.direction == "BUY" else "BUY"

        # Cancel existing SL order if any
        existing = self._active_orders.get(position.id, {})
        sl_order_id = existing.get("sl_order_id")
        if sl_order_id:
            self.dhan.cancel_order(sl_order_id)

        result = self.dhan.place_order(
            security_id=position.security_id,
            exchange_segment=exchange_segment,
            transaction_type=exit_side,
            quantity=position.quantity,
            order_type="MARKET",
            product_type=product_type,
            tag=f"EXIT_{position.id[:12]}",
        )

        if result:
            exit_order_id = result.get("orderId", "")
            closed_pos = self.risk.close_position(
                position.id, exit_price, reason, exit_order_id
            )
            if closed_pos and NOTIFY_ON_TRADE:
                self.notifier.pnl_alert(closed_pos, reason)
            return True

        return False

    # ─────────────────────────────────────────────
    # POSITION MONITORING
    # ─────────────────────────────────────────────

    def monitor_positions(self, price_feed: Dict[str, float]) -> List[str]:
        """
        Check all open positions against latest prices.
        Called on every price update tick.
        Returns list of position IDs that were exited.
        """
        exited = []
        for pos_id, pos in list(self.risk.positions.items()):
            if pos.status != "OPEN":
                continue

            current_price = price_feed.get(pos.security_id, pos.current_price)
            if not current_price:
                continue

            action = self.risk.update_position(pos_id, current_price)
            act = action.get("action", "HOLD")

            if act in ("EXIT_SL", "EXIT_TARGET", "EXIT_TRAIL"):
                success = self.exit_position(pos, current_price, action.get("reason", act))
                if success:
                    exited.append(pos_id)
                    logger.info(f"Position {pos_id} exited: {act} @ {current_price:.2f}")

        return exited

    def square_off_all(self, price_feed: Dict[str, float]):
        """Force-close all open intraday positions (called before market close)."""
        positions_to_close = self.risk.get_positions_to_square_off()
        logger.info(f"⏰ Squaring off {len(positions_to_close)} open positions...")
        for pos in positions_to_close:
            price = price_feed.get(pos.security_id, pos.current_price)
            self.exit_position(pos, price, "EOD_SQUAREOFF")

    # ─────────────────────────────────────────────
    # HELPERS
    # ─────────────────────────────────────────────

    def _get_exchange_segment(self, signal: TradeSignal) -> str:
        if signal.instrument_type in ("CE", "PE"):
            return "NSE_FNO"
        elif signal.instrument_type == "FUT":
            return "NSE_FNO"
        else:
            return "NSE_EQ"

    def _get_exchange_segment_from_pos(self, pos: Position) -> str:
        if pos.instrument_type in ("CE", "PE", "FUT"):
            return "NSE_FNO"
        return "NSE_EQ"

    def _get_product_type(self, signal: TradeSignal) -> str:
        if signal.instrument_type in ("CE", "PE", "FUT"):
            return "INTRADAY"
        return "INTRADAY"


