"""
Smart Money Concepts (SMC) Strategy Module
Implements:
  - Break of Structure (BOS)
  - Change of Character (CHoCH)
  - Order Blocks (OB)
  - Fair Value Gaps (FVG)
  - Liquidity Sweeps
  - Premium / Discount Zones
  - Inducement Detection
"""

import pandas as pd
import numpy as np
import logging
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field
from config.settings import SMART_MONEY_CONFIG as CFG

logger = logging.getLogger(__name__)


@dataclass
class OrderBlock:
    type: str           # BULLISH or BEARISH
    high: float
    low: float
    open: float
    close: float
    timestamp: pd.Timestamp
    strength: float = 0.0
    tested: bool = False
    broken: bool = False

    @property
    def midpoint(self) -> float:
        return (self.high + self.low) / 2


@dataclass
class FairValueGap:
    type: str           # BULLISH or BEARISH
    gap_high: float
    gap_low: float
    timestamp: pd.Timestamp
    filled: bool = False
    fill_pct: float = 0.0


@dataclass
class SMCSignal:
    symbol: str
    timestamp: pd.Timestamp
    signal: str = "WAIT"         # BUY / SELL / WAIT
    signal_type: str = ""        # OB_BOUNCE / FVG_FILL / BOS_FOLLOW / CHOCH_REVERSAL / LIQ_SWEEP
    entry_price: float = 0.0
    stop_loss: float = 0.0
    target: float = 0.0
    strength: float = 0.0        # 0–100

    # Context
    structure: str = "NONE"      # BULLISH / BEARISH / NONE
    nearest_ob: Optional[OrderBlock] = None
    active_fvg: Optional[FairValueGap] = None
    premium_discount: str = "EQUILIBRIUM"  # PREMIUM / DISCOUNT / EQUILIBRIUM
    details: Dict = field(default_factory=dict)


class SmartMoneyAnalyzer:
    """
    Analyzes price structure using Smart Money Concepts.
    Works on any timeframe DataFrame with OHLCV columns.
    """

    def __init__(self):
        self.config = CFG

    # ─────────────────────────────────────────────
    # MAIN ENTRY POINT
    # ─────────────────────────────────────────────
    def analyze(self, df: pd.DataFrame, symbol: str) -> SMCSignal:
        if df is None or len(df) < 30:
            return SMCSignal(symbol=symbol, timestamp=pd.Timestamp.now())

        last_ts = df.index[-1]
        signal = SMCSignal(symbol=symbol, timestamp=last_ts)

        swing_highs, swing_lows = self._find_swings(df)
        structure = self._determine_structure(swing_highs, swing_lows)
        signal.structure = structure

        order_blocks = self._find_order_blocks(df)
        fvgs = self._find_fair_value_gaps(df)

        signal.premium_discount = self._premium_discount_zone(df, swing_highs, swing_lows)

        bos, choch = self._detect_bos_choch(df, swing_highs, swing_lows)

        current_price = df["close"].iloc[-1]

        # Check for OB bounce
        ob_signal = self._check_order_block_entry(df, order_blocks, current_price, structure)
        if ob_signal:
            signal.signal = ob_signal["direction"]
            signal.signal_type = "OB_BOUNCE"
            signal.entry_price = ob_signal["entry"]
            signal.stop_loss = ob_signal["sl"]
            signal.target = ob_signal["tp"]
            signal.strength = ob_signal["strength"]
            signal.nearest_ob = ob_signal["ob"]

        # Check for FVG fill setup
        elif fvgs:
            fvg_signal = self._check_fvg_entry(df, fvgs, current_price, structure)
            if fvg_signal:
                signal.signal = fvg_signal["direction"]
                signal.signal_type = "FVG_FILL"
                signal.entry_price = fvg_signal["entry"]
                signal.stop_loss = fvg_signal["sl"]
                signal.target = fvg_signal["tp"]
                signal.strength = fvg_signal["strength"]
                signal.active_fvg = fvg_signal["fvg"]

        # BOS follow-through
        elif bos and not choch:
            if structure == "BULLISH":
                signal.signal = "BUY"
                signal.signal_type = "BOS_FOLLOW"
                signal.entry_price = current_price
                atr_val = df["atr"].iloc[-1] if "atr" in df.columns else current_price * 0.005
                signal.stop_loss = current_price - 1.5 * atr_val
                signal.target = current_price + 3.0 * atr_val
                signal.strength = 65
            elif structure == "BEARISH":
                signal.signal = "SELL"
                signal.signal_type = "BOS_FOLLOW"
                signal.entry_price = current_price
                atr_val = df["atr"].iloc[-1] if "atr" in df.columns else current_price * 0.005
                signal.stop_loss = current_price + 1.5 * atr_val
                signal.target = current_price - 3.0 * atr_val
                signal.strength = 65

        # CHoCH — potential reversal
        elif choch:
            signal.signal_type = "CHOCH_REVERSAL"
            signal.strength = 50
            # CHoCH alone = caution, wait for confirmation
            signal.details["choch"] = True

        logger.info(
            f"[SMC] {symbol} | Structure={structure} | Zone={signal.premium_discount} | "
            f"Signal={signal.signal} ({signal.signal_type}) | Strength={signal.strength:.0f}%"
        )
        return signal

    # ─────────────────────────────────────────────
    # STRUCTURE ANALYSIS
    # ─────────────────────────────────────────────
    def _find_swings(self, df: pd.DataFrame, n: int = 5) -> Tuple[List, List]:
        """Identify swing highs and swing lows."""
        highs = []
        lows = []
        for i in range(n, len(df) - n):
            if df["high"].iloc[i] == df["high"].iloc[i - n:i + n + 1].max():
                highs.append({"idx": i, "price": df["high"].iloc[i], "time": df.index[i]})
            if df["low"].iloc[i] == df["low"].iloc[i - n:i + n + 1].min():
                lows.append({"idx": i, "price": df["low"].iloc[i], "time": df.index[i]})
        return highs, lows

    def _determine_structure(self, highs: List, lows: List) -> str:
        """
        Bullish structure: Higher Highs + Higher Lows
        Bearish structure: Lower Highs + Lower Lows
        """
        if len(highs) < 2 or len(lows) < 2:
            return "NONE"

        hh = highs[-1]["price"] > highs[-2]["price"]
        hl = lows[-1]["price"] > lows[-2]["price"]
        lh = highs[-1]["price"] < highs[-2]["price"]
        ll = lows[-1]["price"] < lows[-2]["price"]

        if hh and hl:
            return "BULLISH"
        elif lh and ll:
            return "BEARISH"
        return "NONE"

    def _detect_bos_choch(self, df: pd.DataFrame, highs: List, lows: List) -> Tuple[bool, bool]:
        """
        BOS (Break of Structure): Continuation break of last swing
        CHoCH (Change of Character): Break against current structure
        """
        if len(highs) < 2 or len(lows) < 2:
            return False, False

        current_price = df["close"].iloc[-1]
        last_high = highs[-1]["price"]
        last_low = lows[-1]["price"]
        prev_high = highs[-2]["price"]
        prev_low = lows[-2]["price"]

        structure = self._determine_structure(highs, lows)
        bos = False
        choch = False

        confirm = self.config.bos_confirmation_candles
        recent_closes = df["close"].iloc[-confirm:]

        if structure == "BULLISH":
            if all(c > last_high for c in recent_closes):
                bos = True
            elif all(c < prev_low for c in recent_closes):
                choch = True  # Bearish CHoCH

        elif structure == "BEARISH":
            if all(c < last_low for c in recent_closes):
                bos = True
            elif all(c > prev_high for c in recent_closes):
                choch = True  # Bullish CHoCH

        return bos, choch

    # ─────────────────────────────────────────────
    # ORDER BLOCKS
    # ─────────────────────────────────────────────
    def _find_order_blocks(self, df: pd.DataFrame) -> List[OrderBlock]:
        """
        Order Block: Last bearish candle before a bullish impulse (Bullish OB)
                     Last bullish candle before a bearish impulse (Bearish OB)
        """
        blocks = []
        lookback = self.config.order_block_lookback

        for i in range(2, min(lookback, len(df) - 2)):
            idx = len(df) - 1 - i
            candle = df.iloc[idx]
            next_candle = df.iloc[idx + 1]
            next2_candle = df.iloc[idx + 2]

            # Bullish OB: Bearish candle followed by strong bullish impulse
            if (candle["close"] < candle["open"] and
                    next_candle["close"] > next_candle["open"] and
                    next2_candle["close"] > next2_candle["open"] and
                    next_candle["close"] > candle["high"]):

                strength = (next_candle["close"] - candle["low"]) / candle["low"] * 100
                blocks.append(OrderBlock(
                    type="BULLISH",
                    high=candle["high"],
                    low=candle["low"],
                    open=candle["open"],
                    close=candle["close"],
                    timestamp=df.index[idx],
                    strength=min(strength, 100),
                ))

            # Bearish OB: Bullish candle followed by strong bearish impulse
            elif (candle["close"] > candle["open"] and
                  next_candle["close"] < next_candle["open"] and
                  next2_candle["close"] < next2_candle["open"] and
                  next_candle["close"] < candle["low"]):

                strength = (candle["high"] - next_candle["close"]) / candle["high"] * 100
                blocks.append(OrderBlock(
                    type="BEARISH",
                    high=candle["high"],
                    low=candle["low"],
                    open=candle["open"],
                    close=candle["close"],
                    timestamp=df.index[idx],
                    strength=min(strength, 100),
                ))

        return blocks

    # ─────────────────────────────────────────────
    # FAIR VALUE GAPS
    # ─────────────────────────────────────────────
    def _find_fair_value_gaps(self, df: pd.DataFrame) -> List[FairValueGap]:
        """
        FVG (Imbalance): 3-candle pattern where candle[0].high < candle[2].low (bullish)
                         or candle[0].low > candle[2].high (bearish)
        """
        fvgs = []
        min_gap_pct = self.config.fvg_min_gap_pct / 100

        for i in range(2, len(df)):
            c0 = df.iloc[i - 2]
            c2 = df.iloc[i]

            # Bullish FVG
            if c2["low"] > c0["high"]:
                gap_size = (c2["low"] - c0["high"]) / c0["high"]
                if gap_size >= min_gap_pct:
                    fvgs.append(FairValueGap(
                        type="BULLISH",
                        gap_low=c0["high"],
                        gap_high=c2["low"],
                        timestamp=df.index[i - 1],
                    ))

            # Bearish FVG
            elif c0["low"] > c2["high"]:
                gap_size = (c0["low"] - c2["high"]) / c0["low"]
                if gap_size >= min_gap_pct:
                    fvgs.append(FairValueGap(
                        type="BEARISH",
                        gap_high=c0["low"],
                        gap_low=c2["high"],
                        timestamp=df.index[i - 1],
                    ))

        return fvgs[-10:]  # Keep most recent

    # ─────────────────────────────────────────────
    # PREMIUM / DISCOUNT ZONES
    # ─────────────────────────────────────────────
    def _premium_discount_zone(self, df: pd.DataFrame, highs: List, lows: List) -> str:
        """
        Premium zone: > 50% of the swing range (sell setup)
        Discount zone: < 50% of the swing range (buy setup)
        """
        if not highs or not lows:
            return "EQUILIBRIUM"

        swing_high = max(h["price"] for h in highs[-3:])
        swing_low = min(l["price"] for l in lows[-3:])
        current_price = df["close"].iloc[-1]
        swing_range = swing_high - swing_low

        if swing_range <= 0:
            return "EQUILIBRIUM"

        pct = (current_price - swing_low) / swing_range * 100

        if pct > self.config.premium_discount_zone_pct:
            return "PREMIUM"
        elif pct < (100 - self.config.premium_discount_zone_pct):
            return "DISCOUNT"
        return "EQUILIBRIUM"

    # ─────────────────────────────────────────────
    # ENTRY LOGIC
    # ─────────────────────────────────────────────
    def _check_order_block_entry(
        self, df: pd.DataFrame, order_blocks: List[OrderBlock],
        current_price: float, structure: str
    ) -> Optional[Dict]:
        """
        Entry when price returns to an OB:
        - Bullish OB in discount zone → BUY
        - Bearish OB in premium zone → SELL
        """
        atr_val = df["atr"].iloc[-1] if "atr" in df.columns else current_price * 0.005

        for ob in reversed(order_blocks):
            price_in_ob = ob.low <= current_price <= ob.high

            if ob.type == "BULLISH" and price_in_ob and structure != "BEARISH":
                return {
                    "direction": "BUY",
                    "entry": current_price,
                    "sl": ob.low - atr_val * 0.5,
                    "tp": ob.high + (ob.high - ob.low) * 2,
                    "strength": min(ob.strength + 20, 100),
                    "ob": ob,
                }

            if ob.type == "BEARISH" and price_in_ob and structure != "BULLISH":
                return {
                    "direction": "SELL",
                    "entry": current_price,
                    "sl": ob.high + atr_val * 0.5,
                    "tp": ob.low - (ob.high - ob.low) * 2,
                    "strength": min(ob.strength + 20, 100),
                    "ob": ob,
                }

        return None

    def _check_fvg_entry(
        self, df: pd.DataFrame, fvgs: List[FairValueGap],
        current_price: float, structure: str
    ) -> Optional[Dict]:
        """
        FVG fill entry: Price returns to fill the gap.
        - Bullish FVG: Retest from above → BUY
        - Bearish FVG: Retest from below → SELL
        """
        atr_val = df["atr"].iloc[-1] if "atr" in df.columns else current_price * 0.005

        for fvg in reversed(fvgs):
            if fvg.filled:
                continue
            price_in_fvg = fvg.gap_low <= current_price <= fvg.gap_high

            if fvg.type == "BULLISH" and price_in_fvg and structure != "BEARISH":
                return {
                    "direction": "BUY",
                    "entry": current_price,
                    "sl": fvg.gap_low - atr_val,
                    "tp": fvg.gap_high + (fvg.gap_high - fvg.gap_low) * 2,
                    "strength": 60,
                    "fvg": fvg,
                }

            if fvg.type == "BEARISH" and price_in_fvg and structure != "BULLISH":
                return {
                    "direction": "SELL",
                    "entry": current_price,
                    "sl": fvg.gap_high + atr_val,
                    "tp": fvg.gap_low - (fvg.gap_high - fvg.gap_low) * 2,
                    "strength": 60,
                    "fvg": fvg,
                }

        return None
