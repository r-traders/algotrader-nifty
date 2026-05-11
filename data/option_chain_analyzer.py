"""
Option Chain Analyzer
Computes key option chain metrics:
  - PCR (Put-Call Ratio)
  - Max Pain
  - OI Buildup / Unwinding
  - IV Skew, IV Percentile (IVP) & IV Rank (IVR)
  - Call Wall / Put Wall (support/resistance from OI)
  - Gamma Exposure (GEX) approximation
  - Net Delta (dealer positioning approximation)

IV Percentile: % of trading days in past 52 weeks where IV was LOWER than current IV.
  High IVP (>80%) → sell premium (straddle/strangle, iron condor)
  Low IVP (<20%)  → buy options (IV likely to expand)

IV Rank: (Current IV - 52w Low) / (52w High - 52w Low) × 100
  High IVR (>50%) → elevated vol, sell premium
  Low IVR (<25%)  → compressed vol, buy options
"""

import logging
from typing import Dict, List, Optional, Tuple, Deque
from collections import deque
from dataclasses import dataclass, field
from config.settings import OPTION_CHAIN_CONFIG as CFG

logger = logging.getLogger(__name__)


@dataclass
class OptionStrikeData:
    strike: float
    call_ltp: float = 0.0
    call_oi: int = 0
    call_oi_change: int = 0
    call_iv: float = 0.0
    call_delta: float = 0.0
    call_gamma: float = 0.0
    call_theta: float = 0.0
    call_vega: float = 0.0
    call_bid: float = 0.0
    call_ask: float = 0.0
    call_volume: int = 0

    put_ltp: float = 0.0
    put_oi: int = 0
    put_oi_change: int = 0
    put_iv: float = 0.0
    put_delta: float = 0.0
    put_gamma: float = 0.0
    put_theta: float = 0.0
    put_vega: float = 0.0
    put_bid: float = 0.0
    put_ask: float = 0.0
    put_volume: int = 0


@dataclass
class OptionChainSignal:
    symbol: str
    expiry: str
    spot_price: float
    atm_strike: float
    timestamp: str

    # PCR
    pcr_oi: float = 0.0
    pcr_volume: float = 0.0
    pcr_bias: str = "NEUTRAL"        # BULLISH / BEARISH / NEUTRAL

    # Max Pain
    max_pain: float = 0.0
    max_pain_distance_pct: float = 0.0

    # OI Walls
    call_wall: float = 0.0           # Highest call OI strike (resistance)
    put_wall: float = 0.0            # Highest put OI strike (support)

    # IV
    atm_call_iv: float = 0.0
    atm_put_iv: float = 0.0
    iv_skew: float = 0.0             # Call IV - Put IV
    atm_iv_avg: float = 0.0          # Average of ATM call + put IV

    # IV Percentile & Rank (52-week rolling)
    iv_percentile: float = 0.0       # 0–100: % of days IV was lower (high = sell premium)
    iv_rank: float = 0.0             # 0–100: (IV - 52w_low) / (52w_high - 52w_low) × 100
    iv_regime: str = "NORMAL"        # HIGH_IV / LOW_IV / NORMAL
    iv_strategy_hint: str = ""       # "SELL_PREMIUM" / "BUY_OPTIONS" / "NEUTRAL"

    # OI Signals
    calls_longs_added: bool = False
    calls_shorts_added: bool = False
    puts_longs_added: bool = False
    puts_shorts_added: bool = False

    # Overall signal
    signal: str = "WAIT"             # BUY_CE / BUY_PE / SELL_CE / SELL_PE / WAIT
    signal_strength: float = 0.0    # 0–100

    # Strike recommendations
    recommended_ce_strike: Optional[float] = None
    recommended_pe_strike: Optional[float] = None

    details: Dict = field(default_factory=dict)


class OptionChainAnalyzer:
    """
    Analyzes raw option chain data from Dhan API and produces trading signals.
    Tracks rolling 252-day IV history per symbol for IVP/IVR computation.
    """

    IV_HISTORY_DAYS = 252  # ~1 trading year

    def __init__(self):
        self.config = CFG
        self._chain_history: Dict[str, List[OptionChainSignal]] = {}
        # Rolling IV history: symbol → deque of (timestamp, avg_atm_iv) tuples
        self._iv_history: Dict[str, Deque] = {}

    # ─────────────────────────────────────────────
    # MAIN ANALYSIS ENTRY POINT
    # ─────────────────────────────────────────────
    def analyze(self, raw_chain: Dict, symbol: str, expiry: str) -> OptionChainSignal:
        """
        Parse raw Dhan option chain response and generate a signal.
        raw_chain: response from DhanClient.get_option_chain()
        """
        from datetime import datetime
        strikes = self._parse_chain(raw_chain)
        if not strikes:
            logger.warning(f"Empty option chain for {symbol} {expiry}")
            return OptionChainSignal(symbol=symbol, expiry=expiry,
                                     spot_price=0, atm_strike=0, timestamp=str(datetime.now()))

        spot_price = raw_chain.get("last_price", 0.0) or self._estimate_spot(strikes)
        atm_strike = self._find_atm(strikes, spot_price)
        timestamp = str(datetime.now())

        signal = OptionChainSignal(
            symbol=symbol,
            expiry=expiry,
            spot_price=spot_price,
            atm_strike=atm_strike,
            timestamp=timestamp,
        )

        signal.pcr_oi, signal.pcr_volume = self._compute_pcr(strikes)
        signal.pcr_bias = self._pcr_bias(signal.pcr_oi)
        signal.max_pain = self._compute_max_pain(strikes)
        signal.max_pain_distance_pct = abs(spot_price - signal.max_pain) / spot_price * 100
        signal.call_wall, signal.put_wall = self._compute_oi_walls(strikes)
        signal.atm_call_iv, signal.atm_put_iv = self._get_atm_iv(strikes, atm_strike)
        signal.iv_skew = signal.atm_call_iv - signal.atm_put_iv

        # ATM average IV
        call_iv = signal.atm_call_iv
        put_iv  = signal.atm_put_iv
        signal.atm_iv_avg = round((call_iv + put_iv) / 2, 2) if (call_iv + put_iv) > 0 else 0.0

        # IV Percentile & Rank
        self._compute_iv_metrics(symbol, signal)

        self._detect_oi_buildup(strikes, signal)
        self._generate_signal(signal, spot_price, strikes)
        self._recommend_strikes(signal, strikes, spot_price)

        # Store history
        if symbol not in self._chain_history:
            self._chain_history[symbol] = []
        self._chain_history[symbol].append(signal)
        if len(self._chain_history[symbol]) > 100:
            self._chain_history[symbol] = self._chain_history[symbol][-100:]

        self._log_signal(signal)
        return signal

    # ─────────────────────────────────────────────
    # PARSING
    # ─────────────────────────────────────────────
    def _parse_chain(self, raw: Dict) -> List[OptionStrikeData]:
        """Convert Dhan API option chain response to OptionStrikeData list."""
        strikes = []
        data = raw.get("data", raw)  # handle nested structure

        # Dhan returns option chain as list of strike objects
        if isinstance(data, list):
            for item in data:
                strike = float(item.get("strike_price", 0))
                s = OptionStrikeData(strike=strike)

                call = item.get("call_options", {})
                put = item.get("put_options", {})

                s.call_ltp = float(call.get("last_price", 0))
                s.call_oi = int(call.get("oi", 0))
                s.call_oi_change = int(call.get("oi_day_change", 0))
                s.call_iv = float(call.get("implied_volatility", 0))
                s.call_delta = float(call.get("delta", 0))
                s.call_gamma = float(call.get("gamma", 0))
                s.call_theta = float(call.get("theta", 0))
                s.call_vega = float(call.get("vega", 0))
                s.call_volume = int(call.get("volume", 0))

                s.put_ltp = float(put.get("last_price", 0))
                s.put_oi = int(put.get("oi", 0))
                s.put_oi_change = int(put.get("oi_day_change", 0))
                s.put_iv = float(put.get("implied_volatility", 0))
                s.put_delta = float(put.get("delta", 0))
                s.put_gamma = float(put.get("gamma", 0))
                s.put_theta = float(put.get("theta", 0))
                s.put_vega = float(put.get("vega", 0))
                s.put_volume = int(put.get("volume", 0))

                strikes.append(s)

        return sorted(strikes, key=lambda x: x.strike)

    # ─────────────────────────────────────────────
    # METRICS
    # ─────────────────────────────────────────────
    def _estimate_spot(self, strikes: List[OptionStrikeData]) -> float:
        """Estimate spot from put-call parity at ATM."""
        if not strikes:
            return 0.0
        mid = strikes[len(strikes) // 2]
        return mid.strike + mid.call_ltp - mid.put_ltp

    def _find_atm(self, strikes: List[OptionStrikeData], spot: float) -> float:
        if not strikes:
            return 0.0
        return min(strikes, key=lambda x: abs(x.strike - spot)).strike

    def _compute_pcr(self, strikes: List[OptionStrikeData]) -> Tuple[float, float]:
        total_call_oi = sum(s.call_oi for s in strikes)
        total_put_oi = sum(s.put_oi for s in strikes)
        total_call_vol = sum(s.call_volume for s in strikes)
        total_put_vol = sum(s.put_volume for s in strikes)

        pcr_oi = total_put_oi / total_call_oi if total_call_oi > 0 else 1.0
        pcr_vol = total_put_vol / total_call_vol if total_call_vol > 0 else 1.0
        return round(pcr_oi, 3), round(pcr_vol, 3)

    def _pcr_bias(self, pcr_oi: float) -> str:
        if pcr_oi >= self.config.pcr_bullish_threshold:
            return "BULLISH"
        elif pcr_oi <= self.config.pcr_bearish_threshold:
            return "BEARISH"
        return "NEUTRAL"

    def _compute_max_pain(self, strikes: List[OptionStrikeData]) -> float:
        """
        Max Pain = strike where total option sellers' (writers') loss is minimum.
        = Strike where sum of (ITM call value + ITM put value) for all writers is minimized.
        """
        min_loss = float("inf")
        max_pain_strike = 0.0

        for candidate in strikes:
            total_loss = 0.0
            for s in strikes:
                # Writers of calls lose when spot > strike
                if candidate.strike > s.strike:
                    total_loss += (candidate.strike - s.strike) * s.call_oi
                # Writers of puts lose when spot < strike
                if candidate.strike < s.strike:
                    total_loss += (s.strike - candidate.strike) * s.put_oi

            if total_loss < min_loss:
                min_loss = total_loss
                max_pain_strike = candidate.strike

        return max_pain_strike

    def _compute_oi_walls(self, strikes: List[OptionStrikeData]) -> Tuple[float, float]:
        """Identify highest OI call (resistance) and put (support) strikes."""
        if not strikes:
            return 0.0, 0.0
        call_wall = max(strikes, key=lambda s: s.call_oi)
        put_wall = max(strikes, key=lambda s: s.put_oi)
        return call_wall.strike, put_wall.strike

    def _get_atm_iv(self, strikes: List[OptionStrikeData], atm_strike: float) -> Tuple[float, float]:
        for s in strikes:
            if s.strike == atm_strike:
                return s.call_iv, s.put_iv
        return 0.0, 0.0

    def _detect_oi_buildup(self, strikes: List[OptionStrikeData], signal: OptionChainSignal):
        """
        OI Buildup analysis:
        - Long buildup: Price ↑ + OI ↑ (bullish)
        - Short buildup: Price ↓ + OI ↑ (bearish)
        - Long unwinding: Price ↓ + OI ↓ (bearish)
        - Short covering: Price ↑ + OI ↓ (bullish)
        """
        threshold = self.config.oi_buildup_pct_threshold / 100.0

        significant_call_oi_add = [
            s for s in strikes
            if s.call_oi > 0 and (s.call_oi_change / s.call_oi) > threshold
        ]
        significant_put_oi_add = [
            s for s in strikes
            if s.put_oi > 0 and (s.put_oi_change / s.put_oi) > threshold
        ]

        signal.calls_longs_added = len(significant_call_oi_add) > 0
        signal.puts_longs_added = len(significant_put_oi_add) > 0

        signal.details["oi_buildup"] = {
            "call_oi_buildup_strikes": [s.strike for s in significant_call_oi_add[:5]],
            "put_oi_buildup_strikes": [s.strike for s in significant_put_oi_add[:5]],
        }

    # ─────────────────────────────────────────────
    # SIGNAL GENERATION
    # ─────────────────────────────────────────────
    def _generate_signal(self, signal: OptionChainSignal, spot: float,
                         strikes: List[OptionStrikeData]):
        """
        Generate trading signal from option chain data using:
        1. PCR bias
        2. OI walls (support/resistance)
        3. Max pain gravity
        4. IV skew
        5. OI buildup direction
        """
        score = 0.0  # Positive = bullish, Negative = bearish

        # 1. PCR bias contribution
        if signal.pcr_bias == "BULLISH":
            score += 20
        elif signal.pcr_bias == "BEARISH":
            score -= 20

        # 2. Price vs OI walls
        call_wall = signal.call_wall
        put_wall = signal.put_wall
        if put_wall > 0 and spot > put_wall:
            score += 10   # Above put wall = bullish
        if call_wall > 0 and spot < call_wall:
            score += 5    # Below call wall = room to grow
        elif call_wall > 0 and spot > call_wall:
            score -= 10   # Above call wall = overbought risk

        # 3. Max Pain gravity
        if signal.max_pain > 0 and signal.max_pain_distance_pct < self.config.max_pain_buffer_pct:
            score += 0    # Near max pain = range bound, neutral

        # 4. IV Skew
        if signal.iv_skew < -2:
            score += 10   # Put IV > Call IV = smart money hedging = cautious bullish
        elif signal.iv_skew > 2:
            score -= 10   # Call IV > Put IV = bearish skew

        # 5. OI buildup
        if signal.puts_longs_added and not signal.calls_longs_added:
            score += 15   # Put writers adding = confident support
        if signal.calls_longs_added and not signal.puts_longs_added:
            score -= 15   # Call writers adding = resistance

        # Normalize to 0–100 strength
        abs_score = min(abs(score), 60)
        signal.signal_strength = (abs_score / 60) * 100

        if score >= 30:
            signal.signal = "BUY_CE"
        elif score <= -30:
            signal.signal = "BUY_PE"
        elif score >= 15:
            signal.signal = "SELL_PE"
        elif score <= -15:
            signal.signal = "SELL_CE"
        else:
            signal.signal = "WAIT"

        signal.details["score"] = round(score, 2)

    def _recommend_strikes(self, signal: OptionChainSignal, strikes: List[OptionStrikeData],
                           spot: float):
        """
        Recommend specific option strikes to trade.
        For BUY: 1 strike OTM (good premium + leverage)
        For SELL: 1-2 strikes OTM (safer premium collection)
        """
        atm = signal.atm_strike
        otm_call_strikes = [s.strike for s in strikes if s.strike > atm]
        otm_put_strikes = [s.strike for s in strikes if s.strike < atm]

        if otm_call_strikes:
            signal.recommended_ce_strike = otm_call_strikes[0]  # First OTM call
        if otm_put_strikes:
            signal.recommended_pe_strike = otm_put_strikes[-1]  # First OTM put

    # ─────────────────────────────────────────────
    # IV PERCENTILE & IV RANK
    # ─────────────────────────────────────────────
    def _compute_iv_metrics(self, symbol: str, signal: OptionChainSignal):
        """
        Compute IV Percentile (IVP) and IV Rank (IVR) using rolling history.

        IVP: % of past days where IV was LOWER than today's IV.
             High IVP (>80) = elevated IV, favour selling premium.
             Low IVP (<20)  = compressed IV, favour buying options.

        IVR: (Current IV - 52w Low) / (52w High - 52w Low) × 100.
             Normalised rank within the observed range.
        """
        current_iv = signal.atm_iv_avg
        if current_iv <= 0:
            signal.iv_percentile = 0.0
            signal.iv_rank = 0.0
            signal.iv_regime = "NORMAL"
            signal.iv_strategy_hint = "NEUTRAL"
            return

        # Initialise history deque for this symbol
        if symbol not in self._iv_history:
            self._iv_history[symbol] = deque(maxlen=self.IV_HISTORY_DAYS)

        history = self._iv_history[symbol]

        # Append today's reading (deduplicate by hour — avoid spamming same candle)
        from datetime import datetime
        now_hour = datetime.now().replace(minute=0, second=0, microsecond=0)
        if not history or history[-1][0] != now_hour:
            history.append((now_hour, current_iv))

        iv_values = [v for _, v in history]

        if len(iv_values) < 5:
            # Not enough history — return best estimate
            signal.iv_percentile = 50.0
            signal.iv_rank = 50.0
            signal.iv_regime = "NORMAL"
            signal.iv_strategy_hint = "NEUTRAL"
            return

        # IV Percentile: fraction of days with IV below current
        lower_count = sum(1 for v in iv_values if v < current_iv)
        ivp = (lower_count / len(iv_values)) * 100.0

        # IV Rank: normalised position within the observed range
        iv_min = min(iv_values)
        iv_max = max(iv_values)
        if iv_max > iv_min:
            ivr = ((current_iv - iv_min) / (iv_max - iv_min)) * 100.0
        else:
            ivr = 50.0

        signal.iv_percentile = round(ivp, 1)
        signal.iv_rank       = round(ivr, 1)

        # Regime and strategy hint
        if ivp >= 80 or ivr >= 65:
            signal.iv_regime = "HIGH_IV"
            signal.iv_strategy_hint = "SELL_PREMIUM"   # straddle / strangle / IC
        elif ivp <= 20 or ivr <= 25:
            signal.iv_regime = "LOW_IV"
            signal.iv_strategy_hint = "BUY_OPTIONS"    # directional long options
        else:
            signal.iv_regime = "NORMAL"
            signal.iv_strategy_hint = "NEUTRAL"

        logger.debug(
            f"[IV] {symbol} | ATM_IV={current_iv:.1f} | "
            f"IVP={ivp:.1f}% | IVR={ivr:.1f}% | "
            f"Regime={signal.iv_regime} | Hint={signal.iv_strategy_hint} | "
            f"History={len(iv_values)} bars"
        )

    def get_iv_history(self, symbol: str) -> List[Tuple]:
        """Return the raw IV history for a symbol (for dashboard/charts)."""
        history = self._iv_history.get(symbol, deque())
        return [(str(ts), iv) for ts, iv in history]

    def write_iv_cache(self, cache_path: str):
        """
        Write current IV metrics for all analysed symbols to a JSON file.
        The dashboard reads this file to display live IV Percentile / Rank.
        Called by the main engine after each option chain refresh cycle.
        """
        import json, os
        cache = {}
        for symbol, history in self._chain_history.items():
            if not history:
                continue
            latest: OptionChainSignal = history[-1]
            cache[symbol] = {
                "symbol":        symbol,
                "atm_iv":        latest.atm_iv_avg,
                "iv_percentile": latest.iv_percentile,
                "iv_rank":       latest.iv_rank,
                "iv_regime":     latest.iv_regime,
                "hint":          latest.iv_strategy_hint,
                "atm_call_iv":   latest.atm_call_iv,
                "atm_put_iv":    latest.atm_put_iv,
                "iv_skew":       latest.iv_skew,
                "pcr_oi":        latest.pcr_oi,
                "pcr_bias":      latest.pcr_bias,
                "last_updated":  latest.timestamp,
            }
        os.makedirs(os.path.dirname(cache_path), exist_ok=True)
        with open(cache_path, "w") as f:
            json.dump(cache, f, indent=2)
        logger.debug(f"[IV Cache] Written {len(cache)} symbols → {cache_path}")

    # ─────────────────────────────────────────────
    # LOGGING
    # ─────────────────────────────────────────────
    def _log_signal(self, signal: OptionChainSignal):
        logger.info(
            f"[OC] {signal.symbol} | Spot={signal.spot_price:.0f} | "
            f"PCR={signal.pcr_oi:.2f}({signal.pcr_bias}) | "
            f"MaxPain={signal.max_pain:.0f} | "
            f"CallWall={signal.call_wall:.0f} PutWall={signal.put_wall:.0f} | "
            f"ATM_IV={signal.atm_iv_avg:.1f} IVP={signal.iv_percentile:.0f}% "
            f"IVR={signal.iv_rank:.0f}% [{signal.iv_regime}] | "
            f"Signal={signal.signal} Strength={signal.signal_strength:.0f}%"
        )
