"""
Risk Management Module
Enforces all pre-trade and in-trade risk rules:
  - Position sizing (Fixed %, Kelly, Fixed Lots)
  - Daily loss limits
  - Max open positions
  - Stop-loss enforcement
  - Trailing stop management
  - Risk:Reward validation
  - Margin sufficiency check
"""

import logging
import csv
import os
from datetime import datetime, date
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field

from config.settings import RISK_CONFIG as CFG

logger = logging.getLogger(__name__)


@dataclass
class Position:
    """Tracks a live or closed position."""
    id: str
    symbol: str
    exchange: str
    security_id: str
    instrument_type: str        # CE / PE / FUT / EQ
    direction: str              # BUY / SELL
    quantity: int
    entry_price: float
    stop_loss: float
    target_1: float
    target_2: float
    current_price: float = 0.0
    trailing_stop: Optional[float] = None
    status: str = "OPEN"        # OPEN / CLOSED / SL_HIT / TARGET_HIT
    pnl: float = 0.0
    entry_time: datetime = field(default_factory=datetime.now)
    exit_time: Optional[datetime] = None
    exit_price: float = 0.0
    order_id: str = ""
    exit_order_id: str = ""
    strategy: str = ""

    @property
    def unrealised_pnl(self) -> float:
        if self.direction == "BUY":
            return (self.current_price - self.entry_price) * self.quantity
        else:
            return (self.entry_price - self.current_price) * self.quantity

    @property
    def pnl_pct(self) -> float:
        if self.entry_price <= 0:
            return 0.0
        if self.direction == "BUY":
            return (self.current_price - self.entry_price) / self.entry_price * 100
        else:
            return (self.entry_price - self.current_price) / self.entry_price * 100


class RiskManager:
    """
    Central risk management engine.
    Called before every trade and continuously during the session.
    """

    def __init__(self, total_capital: float = 500_000.0):
        self.total_capital = total_capital
        self.available_capital = total_capital
        self.daily_pnl = 0.0
        self.daily_trades = 0
        self.daily_loss_breached = False
        self.positions: Dict[str, Position] = {}
        self._today = date.today()
        self._ensure_log_dirs()

    # ─────────────────────────────────────────────
    # PRE-TRADE CHECKS
    # ─────────────────────────────────────────────

    def can_trade(self, signal) -> Tuple[bool, str]:
        """
        Master pre-trade gate. Returns (allowed: bool, reason: str).
        Checks all risk rules before allowing a trade.
        """
        # Reset daily stats if new day
        self._check_new_day()

        # 1. Daily loss limit
        if self.daily_loss_breached:
            return False, "DAILY_LOSS_LIMIT_BREACHED"

        daily_loss_pct = abs(min(self.daily_pnl, 0)) / self.total_capital * 100
        if daily_loss_pct >= CFG.max_daily_loss_pct:
            self.daily_loss_breached = True
            logger.warning(f"🛑 Daily loss limit hit: {daily_loss_pct:.2f}%")
            return False, f"DAILY_LOSS_LIMIT ({daily_loss_pct:.2f}%)"

        # 2. Max open positions
        open_count = len([p for p in self.positions.values() if p.status == "OPEN"])
        if open_count >= CFG.max_open_positions:
            return False, f"MAX_POSITIONS ({open_count}/{CFG.max_open_positions})"

        # 3. Max positions per symbol
        symbol_positions = len([
            p for p in self.positions.values()
            if p.symbol == signal.symbol and p.status == "OPEN"
        ])
        if symbol_positions >= CFG.max_positions_per_symbol:
            return False, f"MAX_SYMBOL_POSITIONS ({symbol_positions})"

        # 4. Daily trade limit
        if self.daily_trades >= CFG.max_intraday_trades:
            return False, f"MAX_DAILY_TRADES ({self.daily_trades})"

        # 5. Minimum Risk:Reward
        if signal.risk_reward < CFG.risk_reward_min:
            return False, f"POOR_RR ({signal.risk_reward:.2f} < {CFG.risk_reward_min})"

        # 6. Available capital
        required = self.calculate_position_size(signal) * signal.entry_price
        if required > self.available_capital:
            return False, f"INSUFFICIENT_CAPITAL (need ₹{required:,.0f})"

        return True, "OK"

    # ─────────────────────────────────────────────
    # POSITION SIZING
    # ─────────────────────────────────────────────

    def calculate_position_size(self, signal) -> int:
        """
        Returns quantity to trade based on position sizing method.
        """
        method = CFG.position_sizing_method
        entry = signal.entry_price
        sl = signal.stop_loss

        if entry <= 0 or sl <= 0:
            return 0

        risk_per_unit = abs(entry - sl)
        if risk_per_unit <= 0:
            return 1

        if method == "fixed_pct":
            # Risk X% of capital per trade
            risk_amount = self.total_capital * (CFG.max_capital_per_trade_pct / 100)
            qty = int(risk_amount / risk_per_unit)
        elif method == "kelly":
            # Simplified Kelly (needs win_rate and avg_rr from historical data)
            win_rate = 0.50   # Default 50% - update from backtest results
            avg_rr = signal.risk_reward
            kelly_pct = (win_rate * avg_rr - (1 - win_rate)) / avg_rr
            kelly_pct = max(0, min(kelly_pct, 0.25))  # Cap at 25%
            risk_amount = self.total_capital * kelly_pct
            qty = int(risk_amount / risk_per_unit)
        elif method == "fixed_lots":
            qty = 1
        else:
            qty = 1

        return max(qty, 1)

    # ─────────────────────────────────────────────
    # POSITION LIFECYCLE
    # ─────────────────────────────────────────────

    def open_position(self, signal, order_id: str) -> Position:
        """Register a new position after order execution."""
        # Honor pre-set signal.quantity (lot-aware strategy sizing)
        # before falling back to default % risk sizing.
        if getattr(signal, "quantity", 0) and signal.quantity > 0:
            qty = signal.quantity
        else:
            qty = self.calculate_position_size(signal)
        pos = Position(
            id=signal.id,
            symbol=signal.symbol,
            exchange=signal.exchange,
            security_id=signal.security_id,
            instrument_type=signal.instrument_type,
            direction=signal.signal,
            quantity=qty,
            entry_price=signal.entry_price,
            stop_loss=signal.stop_loss,
            target_1=signal.target_1,
            target_2=signal.target_2,
            current_price=signal.entry_price,
            order_id=order_id,
            strategy=signal.strategy_name,
        )

        if CFG.trailing_stop_enabled:
            pos.trailing_stop = signal.stop_loss

        self.positions[pos.id] = pos
        self.available_capital -= qty * signal.entry_price
        self.daily_trades += 1

        logger.info(
            f"✅ Position opened: {signal.symbol} {signal.signal} "
            f"Qty={qty} @ {signal.entry_price:.2f} | SL={signal.stop_loss:.2f} | "
            f"T1={signal.target_1:.2f} | ID={pos.id}"
        )
        self._log_trade("OPEN", pos)
        return pos

    def update_position(self, pos_id: str, current_price: float) -> Dict:
        """
        Update position with latest price.
        Checks SL/target hits and manages trailing stop.
        Returns action dict: {"action": "HOLD"/"EXIT_SL"/"EXIT_TARGET"/"EXIT_TRAIL"}
        """
        pos = self.positions.get(pos_id)
        if not pos or pos.status != "OPEN":
            return {"action": "NOT_FOUND"}

        pos.current_price = current_price
        action = {"action": "HOLD", "reason": ""}

        if pos.direction == "BUY":
            # Check stop loss
            if current_price <= pos.stop_loss:
                action = {"action": "EXIT_SL", "reason": "STOP_LOSS_HIT", "price": current_price}

            # Check target
            elif current_price >= pos.target_1:
                action = {"action": "EXIT_TARGET", "reason": "TARGET_1_HIT", "price": current_price}

            # Trailing stop
            elif CFG.trailing_stop_enabled:
                profit_pct = (current_price - pos.entry_price) / pos.entry_price * 100
                if profit_pct >= CFG.trailing_stop_trigger_pct:
                    new_trail = current_price * (1 - CFG.trailing_stop_distance_pct / 100)
                    if pos.trailing_stop is None or new_trail > pos.trailing_stop:
                        pos.trailing_stop = new_trail
                        pos.stop_loss = new_trail
                        logger.info(f"📈 Trailing stop updated: {pos.symbol} → {new_trail:.2f}")
                if pos.trailing_stop and current_price <= pos.trailing_stop:
                    action = {"action": "EXIT_TRAIL", "reason": "TRAILING_STOP", "price": current_price}

        else:  # SELL
            if current_price >= pos.stop_loss:
                action = {"action": "EXIT_SL", "reason": "STOP_LOSS_HIT", "price": current_price}
            elif current_price <= pos.target_1:
                action = {"action": "EXIT_TARGET", "reason": "TARGET_1_HIT", "price": current_price}
            elif CFG.trailing_stop_enabled:
                profit_pct = (pos.entry_price - current_price) / pos.entry_price * 100
                if profit_pct >= CFG.trailing_stop_trigger_pct:
                    new_trail = current_price * (1 + CFG.trailing_stop_distance_pct / 100)
                    if pos.trailing_stop is None or new_trail < pos.trailing_stop:
                        pos.trailing_stop = new_trail
                        pos.stop_loss = new_trail
                if pos.trailing_stop and current_price >= pos.trailing_stop:
                    action = {"action": "EXIT_TRAIL", "reason": "TRAILING_STOP", "price": current_price}

        return action

    def close_position(self, pos_id: str, exit_price: float, reason: str,
                       exit_order_id: str = "") -> Optional[Position]:
        """Mark a position as closed and update P&L."""
        pos = self.positions.get(pos_id)
        if not pos:
            return None

        pos.exit_price = exit_price
        pos.exit_time = datetime.now()
        pos.exit_order_id = exit_order_id
        pos.status = reason  # SL_HIT / TARGET_HIT / TRAILING_STOP / MANUAL

        if pos.direction == "BUY":
            pos.pnl = (exit_price - pos.entry_price) * pos.quantity
        else:
            pos.pnl = (pos.entry_price - exit_price) * pos.quantity

        self.daily_pnl += pos.pnl
        self.available_capital += pos.quantity * exit_price

        log_status = "🟢 PROFIT" if pos.pnl >= 0 else "🔴 LOSS"
        logger.info(
            f"{log_status}: {pos.symbol} | P&L=₹{pos.pnl:,.0f} ({pos.pnl_pct:.1f}%) | "
            f"Exit={exit_price:.2f} | Reason={reason}"
        )
        self._log_trade("CLOSE", pos)
        return pos

    # ─────────────────────────────────────────────
    # MARKET CLOSE - FORCE EXIT
    # ─────────────────────────────────────────────

    def get_positions_to_square_off(self) -> List[Position]:
        """Returns all open positions that need to be squared off at EOD."""
        return [
            p for p in self.positions.values()
            if p.status == "OPEN" and p.instrument_type in ("CE", "PE", "FUT")
        ]

    # ─────────────────────────────────────────────
    # REPORTING
    # ─────────────────────────────────────────────

    def get_daily_summary(self) -> Dict:
        closed = [p for p in self.positions.values() if p.status != "OPEN"]
        open_pos = [p for p in self.positions.values() if p.status == "OPEN"]
        return {
            "date": str(self._today),
            "total_trades": self.daily_trades,
            "closed_trades": len(closed),
            "open_positions": len(open_pos),
            "daily_pnl": round(self.daily_pnl, 2),
            "daily_pnl_pct": round(self.daily_pnl / self.total_capital * 100, 2),
            "winners": len([p for p in closed if p.pnl > 0]),
            "losers": len([p for p in closed if p.pnl <= 0]),
            "win_rate": (
                len([p for p in closed if p.pnl > 0]) / len(closed) * 100
                if closed else 0
            ),
            "available_capital": round(self.available_capital, 2),
        }

    # ─────────────────────────────────────────────
    # INTERNALS
    # ─────────────────────────────────────────────

    def _check_new_day(self):
        today = date.today()
        if today != self._today:
            logger.info(f"📅 New trading day: {today}. Resetting daily stats.")
            self._today = today
            self.daily_pnl = 0.0
            self.daily_trades = 0
            self.daily_loss_breached = False
            # Remove closed positions from previous day
            self.positions = {
                k: v for k, v in self.positions.items() if v.status == "OPEN"
            }

    def _ensure_log_dirs(self):
        _base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        os.makedirs(os.path.join(_base, "logs"), exist_ok=True)

    def _log_trade(self, event: str, pos: Position):
        """Append trade log to CSV."""
        # Use absolute path so this works regardless of cwd
        _base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        log_file = os.path.join(_base, "logs", "trades.csv")
        header = ["event", "timestamp", "id", "symbol", "direction", "instrument_type",
                  "quantity", "entry_price", "exit_price", "stop_loss", "target_1",
                  "pnl", "strategy", "status", "order_id"]

        row = [
            event, str(datetime.now()), pos.id, pos.symbol, pos.direction,
            pos.instrument_type, pos.quantity, pos.entry_price, pos.exit_price or "",
            pos.stop_loss, pos.target_1, pos.pnl, pos.strategy, pos.status, pos.order_id
        ]

        write_header = not os.path.exists(log_file)
        with open(log_file, "a", newline="") as f:
            writer = csv.writer(f)
            if write_header:
                writer.writerow(header)
            writer.writerow(row)
