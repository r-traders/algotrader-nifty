"""
Technical Indicators Engine
Computes all indicators from OHLCV data.
Uses pandas + numpy (no TA-Lib dependency for portability).
Indicators: EMA, RSI, MACD, Bollinger Bands, ATR, Supertrend, VWAP, Volume Analysis
"""

import numpy as np
import pandas as pd
import logging
from typing import Optional, Dict, List
from config.settings import INDICATOR_CONFIG as CFG

logger = logging.getLogger(__name__)


def candles_to_df(candles: List[Dict]) -> pd.DataFrame:
    """Convert list of OHLCV dicts to a pandas DataFrame."""
    if not candles:
        return pd.DataFrame()
    df = pd.DataFrame(candles)
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    df = df.set_index("timestamp").sort_index()
    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


# ─────────────────────────────────────────────
# MOVING AVERAGES
# ─────────────────────────────────────────────

def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()

def sma(series: pd.Series, period: int) -> pd.Series:
    return series.rolling(window=period).mean()

def wma(series: pd.Series, period: int) -> pd.Series:
    weights = np.arange(1, period + 1)
    return series.rolling(period).apply(lambda x: np.dot(x, weights) / weights.sum(), raw=True)

def vwap(df: pd.DataFrame) -> pd.Series:
    """VWAP calculated for intraday (resets each day)."""
    typical_price = (df["high"] + df["low"] + df["close"]) / 3
    cumulative_tp_vol = (typical_price * df["volume"]).groupby(df.index.date).cumsum()
    cumulative_vol = df["volume"].groupby(df.index.date).cumsum()
    return cumulative_tp_vol / cumulative_vol


# ─────────────────────────────────────────────
# MOMENTUM
# ─────────────────────────────────────────────

def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(com=period - 1, adjust=False).mean()
    avg_loss = loss.ewm(com=period - 1, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))

def macd(series: pd.Series, fast: int = 12, slow: int = 26, signal_period: int = 9):
    """Returns (macd_line, signal_line, histogram)."""
    fast_ema = ema(series, fast)
    slow_ema = ema(series, slow)
    macd_line = fast_ema - slow_ema
    signal_line = ema(macd_line, signal_period)
    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram

def stochastic(df: pd.DataFrame, k_period: int = 14, d_period: int = 3):
    """Returns (%K, %D)."""
    low_min = df["low"].rolling(k_period).min()
    high_max = df["high"].rolling(k_period).max()
    k = 100 * (df["close"] - low_min) / (high_max - low_min)
    d = sma(k, d_period)
    return k, d

def cci(df: pd.DataFrame, period: int = 20) -> pd.Series:
    typical = (df["high"] + df["low"] + df["close"]) / 3
    mean_dev = typical.rolling(period).apply(lambda x: np.mean(np.abs(x - np.mean(x))), raw=True)
    return (typical - sma(typical, period)) / (0.015 * mean_dev)


# ─────────────────────────────────────────────
# VOLATILITY
# ─────────────────────────────────────────────

def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Average True Range."""
    h_l = df["high"] - df["low"]
    h_pc = (df["high"] - df["close"].shift()).abs()
    l_pc = (df["low"] - df["close"].shift()).abs()
    tr = pd.concat([h_l, h_pc, l_pc], axis=1).max(axis=1)
    return tr.ewm(span=period, adjust=False).mean()

def bollinger_bands(series: pd.Series, period: int = 20, std: float = 2.0):
    """Returns (upper_band, middle_band, lower_band, bandwidth, %B)."""
    middle = sma(series, period)
    std_dev = series.rolling(period).std()
    upper = middle + std * std_dev
    lower = middle - std * std_dev
    bandwidth = (upper - lower) / middle * 100
    percent_b = (series - lower) / (upper - lower)
    return upper, middle, lower, bandwidth, percent_b

def keltner_channel(df: pd.DataFrame, ema_period: int = 20, atr_period: int = 10, mult: float = 2.0):
    """Returns (upper, middle, lower)."""
    middle = ema(df["close"], ema_period)
    atr_val = atr(df, atr_period)
    return middle + mult * atr_val, middle, middle - mult * atr_val


# ─────────────────────────────────────────────
# TREND
# ─────────────────────────────────────────────

def supertrend(df: pd.DataFrame, period: int = 10, multiplier: float = 3.0):
    """
    Supertrend indicator.
    Returns (supertrend_series, direction_series)
    direction: 1 = uptrend, -1 = downtrend
    """
    atr_val = atr(df, period)
    hl2 = (df["high"] + df["low"]) / 2
    upper_band = hl2 + multiplier * atr_val
    lower_band = hl2 - multiplier * atr_val

    final_upper = upper_band.copy()
    final_lower = lower_band.copy()

    for i in range(1, len(df)):
        prev_upper = final_upper.iloc[i - 1]
        prev_lower = final_lower.iloc[i - 1]
        prev_close = df["close"].iloc[i - 1]

        if upper_band.iloc[i] < prev_upper or prev_close > prev_upper:
            final_upper.iloc[i] = upper_band.iloc[i]
        else:
            final_upper.iloc[i] = prev_upper

        if lower_band.iloc[i] > prev_lower or prev_close < prev_lower:
            final_lower.iloc[i] = lower_band.iloc[i]
        else:
            final_lower.iloc[i] = prev_lower

    st = pd.Series(index=df.index, dtype=float)
    direction = pd.Series(index=df.index, dtype=int)

    for i in range(1, len(df)):
        close = df["close"].iloc[i]
        prev_st = st.iloc[i - 1] if not pd.isna(st.iloc[i - 1]) else final_upper.iloc[i]
        prev_dir = direction.iloc[i - 1] if not pd.isna(direction.iloc[i - 1]) else -1

        if prev_dir == -1:
            if close > final_upper.iloc[i]:
                st.iloc[i] = final_lower.iloc[i]
                direction.iloc[i] = 1
            else:
                st.iloc[i] = final_upper.iloc[i]
                direction.iloc[i] = -1
        else:
            if close < final_lower.iloc[i]:
                st.iloc[i] = final_upper.iloc[i]
                direction.iloc[i] = -1
            else:
                st.iloc[i] = final_lower.iloc[i]
                direction.iloc[i] = 1

    return st, direction

def adx(df: pd.DataFrame, period: int = 14):
    """Average Directional Index. Returns (adx, +DI, -DI)."""
    up_move = df["high"] - df["high"].shift()
    down_move = df["low"].shift() - df["low"]
    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0)

    tr_val = atr(df, period)  # Reuse ATR
    plus_di = 100 * pd.Series(plus_dm, index=df.index).ewm(span=period, adjust=False).mean() / tr_val
    minus_di = 100 * pd.Series(minus_dm, index=df.index).ewm(span=period, adjust=False).mean() / tr_val
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di)
    adx_val = dx.ewm(span=period, adjust=False).mean()
    return adx_val, plus_di, minus_di


# ─────────────────────────────────────────────
# VOLUME
# ─────────────────────────────────────────────

def volume_analysis(df: pd.DataFrame, ma_period: int = 20) -> Dict[str, pd.Series]:
    """Volume MA and spike detection."""
    vol_ma = sma(df["volume"], ma_period)
    vol_ratio = df["volume"] / vol_ma
    return {"vol_ma": vol_ma, "vol_ratio": vol_ratio}

def obv(df: pd.DataFrame) -> pd.Series:
    """On-Balance Volume."""
    direction = np.sign(df["close"].diff())
    return (direction * df["volume"]).fillna(0).cumsum()

def vpt(df: pd.DataFrame) -> pd.Series:
    """Volume Price Trend."""
    pct_change = df["close"].pct_change()
    return (pct_change * df["volume"]).cumsum()


# ─────────────────────────────────────────────
# SUPPORT & RESISTANCE
# ─────────────────────────────────────────────

def pivot_points(df: pd.DataFrame) -> Dict[str, float]:
    """Classic pivot points from previous candle/day."""
    prev = df.iloc[-2] if len(df) > 1 else df.iloc[-1]
    h, l, c = prev["high"], prev["low"], prev["close"]
    pivot = (h + l + c) / 3
    return {
        "pivot": pivot,
        "r1": 2 * pivot - l,
        "r2": pivot + (h - l),
        "r3": h + 2 * (pivot - l),
        "s1": 2 * pivot - h,
        "s2": pivot - (h - l),
        "s3": l - 2 * (h - pivot),
    }

def cpr(df: pd.DataFrame) -> Dict:
    """
    Central Pivot Range (CPR) — calculated from previous day's H/L/C.

    Levels:
      BC  = (High + Low) / 2                  ← Bottom Central Pivot
      P   = (High + Low + Close) / 3          ← Pivot
      TC  = 2*P - BC                           ← Top Central Pivot
      R1  = 2*P - Low                          ← Resistance 1
      R2  = P + (High - Low)                  ← Resistance 2
      R3  = High + 2*(P - Low)                ← Resistance 3
      S1  = 2*P - High                         ← Support 1
      S2  = P - (High - Low)                  ← Support 2
      S3  = Low - 2*(High - P)                ← Support 3

    CPR Width Signal:
      Narrow CPR (width < 0.2% of price) → Trending day expected
      Wide CPR   (width > 0.5% of price) → Sideways/consolidation expected
    """
    # Find the last completed day's candles
    if df.empty or len(df) < 2:
        return {}

    # Group by date and get previous day's OHLC
    df_day = df.resample("D").agg({"open": "first", "high": "max", "low": "min", "close": "last"}).dropna()
    if len(df_day) < 2:
        return {}

    prev = df_day.iloc[-2]   # previous complete day
    h, l, c = float(prev["high"]), float(prev["low"]), float(prev["close"])

    bc     = (h + l) / 2
    pivot  = (h + l + c) / 3
    tc     = 2 * pivot - bc
    width  = abs(tc - bc)
    width_pct = width / pivot * 100 if pivot > 0 else 0

    # CPR character
    if width_pct < 0.2:
        cpr_type = "NARROW"      # Strong trend day expected
        cpr_bias  = "TRENDING"
    elif width_pct > 0.5:
        cpr_type = "WIDE"        # Sideways/choppy day expected
        cpr_bias  = "SIDEWAYS"
    else:
        cpr_type = "NORMAL"
        cpr_bias  = "NEUTRAL"

    current_close = float(df["close"].iloc[-1])
    if current_close > tc:
        price_vs_cpr = "ABOVE_CPR"    # Bullish
    elif current_close < bc:
        price_vs_cpr = "BELOW_CPR"    # Bearish
    else:
        price_vs_cpr = "INSIDE_CPR"   # Indecision

    return {
        "pivot":        round(pivot, 2),
        "tc":           round(tc, 2),
        "bc":           round(bc, 2),
        "r1":           round(2 * pivot - l, 2),
        "r2":           round(pivot + (h - l), 2),
        "r3":           round(h + 2 * (pivot - l), 2),
        "s1":           round(2 * pivot - h, 2),
        "s2":           round(pivot - (h - l), 2),
        "s3":           round(l - 2 * (h - pivot), 2),
        "cpr_width":    round(width, 2),
        "cpr_width_pct":round(width_pct, 3),
        "cpr_type":     cpr_type,       # NARROW / WIDE / NORMAL
        "cpr_bias":     cpr_bias,       # TRENDING / SIDEWAYS / NEUTRAL
        "price_vs_cpr": price_vs_cpr,   # ABOVE_CPR / BELOW_CPR / INSIDE_CPR
        "prev_high":    round(h, 2),
        "prev_low":     round(l, 2),
        "prev_close":   round(c, 2),
    }


def swing_highs_lows(df: pd.DataFrame, lookback: int = 5) -> Dict:
    """Find recent swing highs and lows."""
    highs = []
    lows = []
    for i in range(lookback, len(df) - lookback):
        window_high = df["high"].iloc[i - lookback:i + lookback + 1]
        window_low = df["low"].iloc[i - lookback:i + lookback + 1]
        if df["high"].iloc[i] == window_high.max():
            highs.append({"idx": i, "price": df["high"].iloc[i], "time": df.index[i]})
        if df["low"].iloc[i] == window_low.min():
            lows.append({"idx": i, "price": df["low"].iloc[i], "time": df.index[i]})
    return {"swing_highs": highs[-5:], "swing_lows": lows[-5:]}


# ─────────────────────────────────────────────
# COMPOSITE INDICATOR BUILDER
# ─────────────────────────────────────────────

def add_all_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """
    Adds all commonly used indicators to a dataframe.
    Returns enriched DataFrame with all indicator columns.
    """
    if df.empty or len(df) < 30:
        return df

    c = df["close"]

    # EMAs
    df["ema9"] = ema(c, CFG.ema_fast)
    df["ema21"] = ema(c, CFG.ema_medium)
    df["ema50"] = ema(c, CFG.ema_slow)
    df["ema200"] = ema(c, CFG.ema_trend)

    # RSI
    df["rsi"] = rsi(c, CFG.rsi_period)

    # MACD
    df["macd"], df["macd_signal"], df["macd_hist"] = macd(c, CFG.macd_fast, CFG.macd_slow, CFG.macd_signal)

    # Bollinger Bands
    df["bb_upper"], df["bb_mid"], df["bb_lower"], df["bb_bw"], df["bb_pct"] = bollinger_bands(c, CFG.bb_period, CFG.bb_std)

    # ATR
    df["atr"] = atr(df, CFG.atr_period)

    # Supertrend
    df["supertrend"], df["st_direction"] = supertrend(df, CFG.supertrend_period, CFG.supertrend_multiplier)

    # ADX
    df["adx"], df["plus_di"], df["minus_di"] = adx(df)

    # VWAP
    if CFG.vwap_enabled:
        try:
            df["vwap"] = vwap(df)
        except Exception:
            pass

    # Volume
    vol = volume_analysis(df, CFG.volume_ma_period)
    df["vol_ma"] = vol["vol_ma"]
    df["vol_ratio"] = vol["vol_ratio"]
    df["obv"] = obv(df)

    # Stochastic
    df["stoch_k"], df["stoch_d"] = stochastic(df)

    return df


def get_indicator_summary(df: pd.DataFrame) -> Dict:
    """
    Returns the latest bar's indicator values as a summary dict.
    Useful for strategy signal generation.
    """
    if df.empty:
        return {}

    last = df.iloc[-1]
    prev = df.iloc[-2] if len(df) > 1 else last

    summary = {}

    # Trend direction
    summary["ema_trend"] = (
        "BULLISH" if last.get("ema9", 0) > last.get("ema21", 0) > last.get("ema50", 0)
        else "BEARISH" if last.get("ema9", 0) < last.get("ema21", 0) < last.get("ema50", 0)
        else "MIXED"
    )

    # RSI
    rsi_val = last.get("rsi", 50)
    summary["rsi"] = rsi_val
    summary["rsi_signal"] = (
        "OVERSOLD" if rsi_val < CFG.rsi_oversold
        else "OVERBOUGHT" if rsi_val > CFG.rsi_overbought
        else "NEUTRAL"
    )

    # MACD
    macd_hist = last.get("macd_hist", 0)
    prev_hist = prev.get("macd_hist", 0)
    summary["macd_hist"] = macd_hist
    summary["macd_cross"] = (
        "BULLISH_CROSS" if prev_hist < 0 < macd_hist
        else "BEARISH_CROSS" if prev_hist > 0 > macd_hist
        else "NO_CROSS"
    )

    # Bollinger Bands
    bb_pct = last.get("bb_pct", 0.5)
    summary["bb_pct"] = bb_pct
    summary["bb_signal"] = (
        "OVERSOLD" if bb_pct < 0.05
        else "OVERBOUGHT" if bb_pct > 0.95
        else "NEUTRAL"
    )

    # Supertrend
    summary["supertrend_dir"] = "BULLISH" if last.get("st_direction", -1) == 1 else "BEARISH"

    # Volume
    summary["vol_ratio"] = last.get("vol_ratio", 1.0)
    summary["vol_spike"] = last.get("vol_ratio", 1.0) > CFG.volume_spike_multiplier

    # ADX
    adx_val = last.get("adx", 20)
    summary["adx"] = adx_val
    summary["trend_strength"] = "STRONG" if adx_val > 25 else "WEAK"

    return summary
