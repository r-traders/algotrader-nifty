"""
Signal Aggregator
Combines signals from:
  1. Option Chain Analysis  (30 pts)
  2. Technical Indicators   (25 pts)
  3. Smart Money Concepts   (20 pts)
  4. CPR (Central Pivot)    (10 pts)
  5. IV Volatility          (10 pts)
  6. News Sentiment         ( 5 pts)
                         = 100 pts total

Produces a final HIGH-CONFIDENCE trade signal with entry, SL, target.
"""

import logging
import pandas as pd
from typing import Dict, Optional, List
from dataclasses import dataclass, field
from datetime import datetime

from data.option_chain_analyzer import OptionChainSignal
from strategies.smart_money import SMCSignal
from data.indicators import get_indicator_summary, cpr
from data.news_sentiment import NewsSentimentAnalyzer, get_news_signal
from config.settings import INDICATOR_CONFIG as IND_CFG, RISK_CONFIG, ENABLED_STRATEGIES

logger = logging.getLogger(__name__)


@dataclass
class TradeSignal:
    """Final aggregated trade signal ready for execution."""
    id: str
    symbol: str
    exchange: str
    security_id: str
    timestamp: datetime

    # Signal
    signal: str = "WAIT"         # BUY / SELL / WAIT
    instrument_type: str = ""    # CE / PE / FUT / EQ
    strike: Optional[float] = None
    expiry: Optional[str] = None

    # Prices
    entry_price: float = 0.0
    stop_loss: float = 0.0
    target_1: float = 0.0
    target_2: float = 0.0
    risk_reward: float = 0.0

    # Confidence
    confidence: float = 0.0      # 0–100
    signal_sources: List[str] = field(default_factory=list)

    # Context
    timeframe: str = ""
    strategy_name: str = ""
    notes: str = ""

    # Execution
    quantity: int = 0
    executed: bool = False
    execution_time: Optional[datetime] = None

    @property
    def is_valid(self) -> bool:
        """A signal is actionable if confidence > 60 and all price levels are set."""
        return (
            self.signal in ("BUY", "SELL") and
            self.confidence >= 60 and
            self.entry_price > 0 and
            self.stop_loss > 0 and
            self.target_1 > 0 and
            self.risk_reward >= RISK_CONFIG.risk_reward_min
        )


class SignalAggregator:
    """
    Aggregates multiple signal sources into a single high-confidence trade decision.

    Scoring logic (max 100 points):
      - Option Chain signal:      0–30 pts
      - Technical indicators:     0–25 pts
      - Smart Money Concepts:     0–20 pts
      - CPR (Central Pivot):      0–10 pts
      - IV Volatility:            0–10 pts
      - News Sentiment:           0– 5 pts

    Signal fires when total score >= SIGNAL_THRESHOLD (default 60).
    """

    SIGNAL_THRESHOLD = 60

    def __init__(self, threshold: float = 60.0):
        self.SIGNAL_THRESHOLD = threshold
        self._news_analyzer = NewsSentimentAnalyzer()   # shared, cached 15 min

    def aggregate(
        self,
        symbol: str,
        exchange: str,
        security_id: str,
        df: pd.DataFrame,                              # Indicator-enriched OHLCV
        oc_signal: Optional[OptionChainSignal] = None, # Option chain signal
        smc_signal: Optional[SMCSignal] = None,        # SMC signal
        timeframe: str = "5min",
        expiry: Optional[str] = None,
    ) -> TradeSignal:
        """
        Produces an aggregated TradeSignal from all available sources.
        """
        import time
        sig_id = f"{symbol}_{int(time.time())}"
        result = TradeSignal(
            id=sig_id,
            symbol=symbol,
            exchange=exchange,
            security_id=security_id,
            timestamp=datetime.now(),
            timeframe=timeframe,
            expiry=expiry,
        )

        ind_summary = get_indicator_summary(df)
        current_price = df["close"].iloc[-1] if not df.empty else 0.0
        atr_val = df["atr"].iloc[-1] if ("atr" in df.columns and not df.empty) else current_price * 0.005

        # Compute CPR for current data
        cpr_data = cpr(df) if not df.empty else {}

        bull_score = 0.0
        bear_score = 0.0
        sources = []

        # ── 1. OPTION CHAIN (30 pts max) ──
        if oc_signal and ENABLED_STRATEGIES.get("option_chain_momentum"):
            oc_points = self._score_option_chain(oc_signal)
            if oc_points > 0:
                bull_score += oc_points
                sources.append(f"OC:+{oc_points:.0f}")
            elif oc_points < 0:
                bear_score += abs(oc_points)
                sources.append(f"OC:{oc_points:.0f}")

        # ── 2. TECHNICAL INDICATORS (25 pts max) ──
        if ENABLED_STRATEGIES.get("technical_breakout"):
            ind_bull, ind_bear, ind_sources = self._score_indicators(ind_summary)
            bull_score += ind_bull
            bear_score += ind_bear
            sources.extend(ind_sources)

        # ── 3. SMART MONEY (20 pts max) ──
        if smc_signal and ENABLED_STRATEGIES.get("smart_money_order_block"):
            smc_bull, smc_bear = self._score_smc(smc_signal)
            bull_score += smc_bull
            bear_score += smc_bear
            if smc_bull > 0:
                sources.append(f"SMC:BULL+{smc_bull:.0f}")
            elif smc_bear > 0:
                sources.append(f"SMC:BEAR+{smc_bear:.0f}")

        # ── 4. CPR (10 pts max) ──
        cpr_bull, cpr_bear, cpr_src = self._score_cpr(cpr_data, current_price)
        bull_score += cpr_bull
        bear_score += cpr_bear
        if cpr_src:
            sources.append(cpr_src)

        # ── 5. IV VOLATILITY (10 pts max) ──
        if oc_signal:
            iv_bull, iv_bear, iv_src = self._score_iv(oc_signal)
            bull_score += iv_bull
            bear_score += iv_bear
            if iv_src:
                sources.append(iv_src)

        # ── 6. NEWS SENTIMENT (5 pts max) ──
        news_bull, news_bear, news_src = self._score_news()
        bull_score += news_bull
        bear_score += news_bear
        if news_src:
            sources.append(news_src)

        # ── 7. PRICE ACTION (10 pts max) — candlestick + VWAP ──
        pa_bull, pa_bear = self._score_price_action(df, ind_summary)
        bull_score += pa_bull
        bear_score += pa_bear
        if pa_bull > 0:
            sources.append(f"PA:BULL+{pa_bull:.0f}")
        elif pa_bear > 0:
            sources.append(f"PA:BEAR+{pa_bear:.0f}")

        # ── ADAPTIVE THRESHOLD ──
        # If key data sources (OC, SMC) are unavailable, lower the bar proportionally
        # so that technical + CPR + price action can still fire signals.
        max_possible = 110.0  # 30+25+20+10+10+5+10
        achieved_max = 0.0
        achieved_max += 30 if oc_signal else 0
        achieved_max += 25   # indicators always available
        achieved_max += 20 if smc_signal else 0
        achieved_max += 10   # CPR always available
        achieved_max += 10 if oc_signal else 0   # IV needs OC
        achieved_max += 5    # news always available
        achieved_max += 10   # price action always available
        # Scale threshold down proportionally when data is missing
        data_coverage = achieved_max / max_possible
        adaptive_threshold = max(45.0, self.SIGNAL_THRESHOLD * data_coverage)

        # ── DECISION ──
        result.signal_sources = sources
        net_bull = bull_score
        net_bear = bear_score

        if net_bull >= adaptive_threshold and net_bull > net_bear:
            result.signal = "BUY"
            result.confidence = min(net_bull, 100)
            result.entry_price = current_price
            result.stop_loss = current_price - 1.5 * atr_val
            result.target_1 = current_price + 2.5 * atr_val
            result.target_2 = current_price + 4.0 * atr_val

            if oc_signal and oc_signal.signal in ("BUY_CE", "SELL_PE"):
                result.instrument_type = "CE"
                result.strike = oc_signal.recommended_ce_strike
            else:
                result.instrument_type = "EQ"

        elif net_bear >= adaptive_threshold and net_bear > net_bull:
            result.signal = "SELL"
            result.confidence = min(net_bear, 100)
            result.entry_price = current_price
            result.stop_loss = current_price + 1.5 * atr_val
            result.target_1 = current_price - 2.5 * atr_val
            result.target_2 = current_price - 4.0 * atr_val

            if oc_signal and oc_signal.signal in ("BUY_PE", "SELL_CE"):
                result.instrument_type = "PE"
                result.strike = oc_signal.recommended_pe_strike
            else:
                result.instrument_type = "EQ"

        else:
            result.signal = "WAIT"
            result.confidence = max(net_bull, net_bear)

        # Risk:Reward calculation
        if result.entry_price > 0 and result.stop_loss > 0 and result.target_1 > 0:
            risk = abs(result.entry_price - result.stop_loss)
            reward = abs(result.target_1 - result.entry_price)
            result.risk_reward = round(reward / risk, 2) if risk > 0 else 0

        result.strategy_name = "MULTI_STRATEGY_AGGREGATED"
        result.notes = " | ".join(sources)

        # Log scoring breakdown so we can diagnose near-misses
        logger.debug(
            f"[SCORE] {symbol} | BULL={net_bull:.1f} BEAR={net_bear:.1f} "
            f"threshold={adaptive_threshold:.1f} (cov={data_coverage:.0%}) | "
            f"sources={sources}"
        )

        self._log(result)
        return result

    # ─────────────────────────────────────────────
    # SCORING METHODS
    # ─────────────────────────────────────────────

    def _score_option_chain(self, oc: OptionChainSignal) -> float:
        """Returns positive score for bullish, negative for bearish. Max ±30."""
        score = 0.0
        strength_factor = oc.signal_strength / 100.0

        if oc.signal in ("BUY_CE", "SELL_PE"):
            score = 30 * strength_factor
        elif oc.signal in ("BUY_PE", "SELL_CE"):
            score = -30 * strength_factor
        elif oc.pcr_bias == "BULLISH":
            score = 12
        elif oc.pcr_bias == "BEARISH":
            score = -12

        return round(score, 1)

    def _score_indicators(self, ind: Dict) -> tuple:
        """Returns (bull_pts, bear_pts, sources). Max 25 each."""
        bull = 0.0
        bear = 0.0
        sources = []

        # EMA trend (8 pts)
        ema_trend = ind.get("ema_trend", "MIXED")
        if ema_trend == "BULLISH":
            bull += 8
            sources.append("EMA:BULL")
        elif ema_trend == "BEARISH":
            bear += 8
            sources.append("EMA:BEAR")

        # RSI (5 pts)
        rsi_sig = ind.get("rsi_signal", "NEUTRAL")
        if rsi_sig == "OVERSOLD":
            bull += 5
            sources.append("RSI:OVERSOLD")
        elif rsi_sig == "OVERBOUGHT":
            bear += 5
            sources.append("RSI:OVERBUY")

        # MACD cross (8 pts)
        macd_cross = ind.get("macd_cross", "NO_CROSS")
        if macd_cross == "BULLISH_CROSS":
            bull += 8
            sources.append("MACD:BULL")
        elif macd_cross == "BEARISH_CROSS":
            bear += 8
            sources.append("MACD:BEAR")
        elif ind.get("macd_hist", 0) > 0:
            bull += 2
        elif ind.get("macd_hist", 0) < 0:
            bear += 2

        # Supertrend (6 pts)
        st_dir = ind.get("supertrend_dir", "")
        if st_dir == "BULLISH":
            bull += 6
            sources.append("ST:BULL")
        elif st_dir == "BEARISH":
            bear += 6
            sources.append("ST:BEAR")

        # Volume spike confirmation (bonus up to 3 pts)
        if ind.get("vol_spike") and ind.get("ema_trend") == "BULLISH":
            bull += 3
            sources.append("VOL:SPIKE_BULL")
        elif ind.get("vol_spike") and ind.get("ema_trend") == "BEARISH":
            bear += 3
            sources.append("VOL:SPIKE_BEAR")

        return round(min(bull, 25), 1), round(min(bear, 25), 1), sources

    def _score_cpr(self, cpr_data: Dict, current_price: float) -> tuple:
        """
        Score using CPR (Central Pivot Range).  Max ±10 pts.

        Logic:
          • NARROW CPR  (<0.2%) = trending day expected.
              – Price ABOVE_CPR → BULL boost (+8)
              – Price BELOW_CPR → BEAR boost (+8)
          • WIDE CPR (>0.5%) = sideways / range-bound day.
              – Reduces confidence (small penalty –3 each side)
          • Price crossing Pivot = directional confirmation (+5)
          • Price near R1/S1 = caution (slight contra penalty)
        Returns (bull_pts, bear_pts, source_label)
        """
        if not cpr_data:
            return 0.0, 0.0, ""

        bull = 0.0
        bear = 0.0
        cpr_type   = cpr_data.get("cpr_type", "NORMAL")     # NARROW / WIDE / NORMAL
        price_pos  = cpr_data.get("price_vs_cpr", "")        # ABOVE_CPR / BELOW_CPR / INSIDE_CPR
        pivot      = cpr_data.get("pivot", 0.0)
        r1         = cpr_data.get("r1", 0.0)
        s1         = cpr_data.get("s1", 0.0)

        # Narrow CPR = trending day — direction matters
        if cpr_type == "NARROW":
            if price_pos == "ABOVE_CPR":
                bull += 8
            elif price_pos == "BELOW_CPR":
                bear += 8
            elif price_pos == "INSIDE_CPR" and current_price > pivot:
                bull += 4
            elif price_pos == "INSIDE_CPR" and current_price <= pivot:
                bear += 4

        # Normal CPR
        elif cpr_type == "NORMAL":
            if price_pos == "ABOVE_CPR":
                bull += 5
            elif price_pos == "BELOW_CPR":
                bear += 5

        # Wide CPR = sideways — range bound, reduce signal confidence
        elif cpr_type == "WIDE":
            # Small reversal signals near edges
            if r1 > 0 and current_price >= r1 * 0.999:
                bear += 3   # Hitting resistance — potential reversal
            elif s1 > 0 and current_price <= s1 * 1.001:
                bull += 3   # Hitting support — potential bounce

        label = f"CPR:{cpr_type}({price_pos})+{max(bull,bear):.0f}"
        return round(min(bull, 10), 1), round(min(bear, 10), 1), label

    def _score_iv(self, oc: OptionChainSignal) -> tuple:
        """
        Score using IV Percentile and IV Rank.  Max 10 pts each side.

        HIGH_IV  (IVP>80 or IVR>65) → favour SELL signals, penalise BUY signals.
        LOW_IV   (IVP<20 or IVR<25) → favour BUY signals, neutral on SELL.
        NORMAL   → small boost to dominant signal direction.
        Returns (bull_pts, bear_pts, source_label)
        """
        bull = 0.0
        bear = 0.0
        ivp    = getattr(oc, "iv_percentile", 0.0)
        ivr    = getattr(oc, "iv_rank", 0.0)
        regime = getattr(oc, "iv_regime", "NORMAL")
        hint   = getattr(oc, "iv_strategy_hint", "NEUTRAL")

        if not ivp and not ivr:
            return 0.0, 0.0, ""

        if regime == "HIGH_IV":
            # High vol regime: selling premium makes sense
            # Amplify SELL signals (option chain direction)
            if oc.signal in ("BUY_PE", "SELL_CE"):
                bear += 10      # High IV + bearish OC = strong SELL signal
            elif oc.signal in ("BUY_CE", "SELL_PE"):
                bull += 5       # High IV dampens BUY — only partial credit
            else:
                # No clear OC signal — neutral
                pass

        elif regime == "LOW_IV":
            # Low vol regime: buying options is cheap, expect expansion
            if oc.signal in ("BUY_CE", "SELL_PE"):
                bull += 10      # Low IV + bullish OC = buy CE
            elif oc.signal in ("BUY_PE", "SELL_CE"):
                bear += 10      # Low IV + bearish OC = buy PE
            else:
                pass

        else:  # NORMAL
            # Small boost in dominant OC direction
            if oc.signal in ("BUY_CE", "SELL_PE"):
                bull += 5
            elif oc.signal in ("BUY_PE", "SELL_CE"):
                bear += 5

        label = f"IV:{regime}(IVP={ivp:.0f}%,IVR={ivr:.0f}%)+{max(bull,bear):.0f}"
        return round(min(bull, 10), 1), round(min(bear, 10), 1), label

    def _score_news(self) -> tuple:
        """
        Score using news sentiment.  Max 5 pts each side.
        Returns (bull_pts, bear_pts, source_label)
        """
        try:
            news = get_news_signal(self._news_analyzer)
            direction  = news.get("signal", "NEUTRAL")
            pts        = abs(news.get("points", 0.0))
            pts_capped = min(pts, 5.0)
            err        = news.get("error")

            if err or direction == "NEUTRAL":
                return 0.0, 0.0, ""

            summary = news.get("summary", "News")[:40]
            if direction == "BULL":
                return round(pts_capped, 1), 0.0, f"NEWS:BULL+{pts_capped:.1f}"
            elif direction == "BEAR":
                return 0.0, round(pts_capped, 1), f"NEWS:BEAR+{pts_capped:.1f}"
        except Exception as e:
            logger.debug(f"[SignalAgg] News scoring failed: {e}")
        return 0.0, 0.0, ""

    def _score_smc(self, smc: SMCSignal) -> tuple:
        """Returns (bull_pts, bear_pts). Max 20 each."""
        if smc.signal == "BUY":
            return min(smc.strength * 0.2, 20), 0
        elif smc.signal == "SELL":
            return 0, min(smc.strength * 0.2, 20)
        elif smc.structure == "BULLISH":
            return 8, 0
        elif smc.structure == "BEARISH":
            return 0, 8
        return 0, 0

    def _score_price_action(self, df: pd.DataFrame, ind: Dict) -> tuple:
        """Returns (bull_pts, bear_pts). Max 10 each."""
        if df.empty or len(df) < 3:
            return 0, 0

        bull = 0.0
        bear = 0.0

        last = df.iloc[-1]
        prev = df.iloc[-2]

        # Candlestick patterns (basic)
        # Bullish engulfing
        if (prev["close"] < prev["open"] and
                last["close"] > last["open"] and
                last["close"] > prev["open"] and
                last["open"] < prev["close"]):
            bull += 7

        # Bearish engulfing
        elif (prev["close"] > prev["open"] and
              last["close"] < last["open"] and
              last["close"] < prev["open"] and
              last["open"] > prev["close"]):
            bear += 7

        # Pin bar / Hammer (bullish)
        body = abs(last["close"] - last["open"])
        lower_wick = min(last["close"], last["open"]) - last["low"]
        upper_wick = last["high"] - max(last["close"], last["open"])
        if body > 0 and lower_wick >= 2 * body and upper_wick <= 0.5 * body:
            bull += 5

        # Shooting star (bearish)
        if body > 0 and upper_wick >= 2 * body and lower_wick <= 0.5 * body:
            bear += 5

        # VWAP price position
        if "vwap" in df.columns:
            vwap_val = last.get("vwap", 0)
            if vwap_val > 0:
                if last["close"] > vwap_val:
                    bull += 3
                else:
                    bear += 3

        return min(bull, 10), min(bear, 10)

    # ─────────────────────────────────────────────
    # LOGGING
    # ─────────────────────────────────────────────
    def _log(self, sig: TradeSignal):
        if sig.signal != "WAIT":
            logger.info(
                f"[SIGNAL] {sig.symbol} | {sig.signal} {sig.instrument_type} | "
                f"Entry={sig.entry_price:.2f} SL={sig.stop_loss:.2f} T1={sig.target_1:.2f} | "
                f"Conf={sig.confidence:.0f}% R:R={sig.risk_reward} | "
                f"Sources: {sig.notes}"
            )
        else:
            logger.debug(f"[SIGNAL] {sig.symbol} → WAIT (max conf: {sig.confidence:.0f}%)")
