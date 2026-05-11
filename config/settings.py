"""
Trading System Configuration
Dhan API - Indian Stock Market Automated Trading
"""

import os
from dataclasses import dataclass, field
from typing import List, Dict

# ─────────────────────────────────────────────
# DHAN API CREDENTIALS (set via environment variables or .env file)
# ─────────────────────────────────────────────
DHAN_CLIENT_ID = os.getenv("DHAN_CLIENT_ID", "")
DHAN_ACCESS_TOKEN = os.getenv("DHAN_ACCESS_TOKEN", "")

# ─────────────────────────────────────────────
# TRADING MODE
# ─────────────────────────────────────────────
PAPER_TRADING = os.getenv("PAPER_TRADING", "true").lower() == "true"  # Set to False for live trading

# ─────────────────────────────────────────────
# MARKET HOURS (IST)
# ─────────────────────────────────────────────
MARKET_OPEN_TIME = "09:15"
MARKET_CLOSE_TIME = "15:30"
PRE_MARKET_TIME = "09:00"
AVOID_TRADE_NEAR_CLOSE_MINS = 15   # Avoid new trades 15 min before close
AVOID_TRADE_NEAR_OPEN_MINS = 5     # Avoid trades in first 5 min

# ─────────────────────────────────────────────
# INSTRUMENTS UNIVERSE
# ─────────────────────────────────────────────
INDEX_SYMBOLS = ["NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY"]
DEFAULT_EXPIRY_TYPE = "WEEKLY"   # WEEKLY or MONTHLY

WATCHLIST_EQUITIES = [
    "RELIANCE", "TCS", "HDFCBANK", "INFY", "ICICIBANK",
    "SBIN", "BAJFINANCE", "WIPRO", "AXISBANK", "KOTAKBANK",
    "LT", "HCLTECH", "ASIANPAINT", "MARUTI", "TITAN"
]

# ─────────────────────────────────────────────
# RISK MANAGEMENT
# ─────────────────────────────────────────────
@dataclass
class RiskConfig:
    max_capital_per_trade_pct: float = 2.0          # Max 2% of capital per trade
    max_daily_loss_pct: float = 3.0                  # Stop trading if -3% daily loss
    max_open_positions: int = 5                       # Max simultaneous positions
    max_positions_per_symbol: int = 2                 # Max positions per instrument
    default_stop_loss_pct: float = 1.5               # 1.5% SL by default
    default_target_pct: float = 3.0                  # 3% target by default
    trailing_stop_enabled: bool = True
    trailing_stop_trigger_pct: float = 1.5           # Activate trailing stop after 1.5% profit
    trailing_stop_distance_pct: float = 0.75         # Trail by 0.75%
    max_intraday_trades: int = 20                     # Max trades per day
    risk_reward_min: float = 1.5                      # Minimum R:R ratio before entry
    position_sizing_method: str = "fixed_pct"         # fixed_pct | kelly | fixed_lots

RISK_CONFIG = RiskConfig()

# ─────────────────────────────────────────────
# OPTION CHAIN ANALYSIS CONFIG
# ─────────────────────────────────────────────
@dataclass
class OptionChainConfig:
    pcr_bullish_threshold: float = 1.2               # PCR > 1.2 → bullish
    pcr_bearish_threshold: float = 0.8               # PCR < 0.8 → bearish
    oi_buildup_pct_threshold: float = 10.0           # OI change > 10% is significant
    iv_percentile_high: float = 70.0                 # IV Percentile > 70 = expensive
    iv_percentile_low: float = 30.0                  # IV Percentile < 30 = cheap
    max_pain_buffer_pct: float = 0.5                 # Max Pain ± 0.5%
    strike_range_count: int = 10                     # Number of strikes above/below ATM
    refresh_interval_secs: int = 60                  # Refresh option chain every 60s

OPTION_CHAIN_CONFIG = OptionChainConfig()

# ─────────────────────────────────────────────
# TECHNICAL INDICATORS CONFIG
# ─────────────────────────────────────────────
@dataclass
class IndicatorConfig:
    # Moving Averages
    ema_fast: int = 9
    ema_medium: int = 21
    ema_slow: int = 50
    ema_trend: int = 200

    # RSI
    rsi_period: int = 14
    rsi_overbought: float = 70.0
    rsi_oversold: float = 30.0

    # MACD
    macd_fast: int = 12
    macd_slow: int = 26
    macd_signal: int = 9

    # Bollinger Bands
    bb_period: int = 20
    bb_std: float = 2.0

    # ATR
    atr_period: int = 14

    # Supertrend
    supertrend_period: int = 10
    supertrend_multiplier: float = 3.0

    # VWAP
    vwap_enabled: bool = True

    # Volume
    volume_ma_period: int = 20
    volume_spike_multiplier: float = 2.0

INDICATOR_CONFIG = IndicatorConfig()

# ─────────────────────────────────────────────
# SMART MONEY CONCEPTS CONFIG
# ─────────────────────────────────────────────
@dataclass
class SmartMoneyConfig:
    order_block_lookback: int = 20          # Candles to look back for Order Blocks
    fvg_min_gap_pct: float = 0.1            # Minimum Fair Value Gap size (%)
    bos_confirmation_candles: int = 2       # Candles needed to confirm Break of Structure
    choch_enabled: bool = True              # Change of Character detection
    liquidity_sweep_buffer_pct: float = 0.05  # Buffer for liquidity sweep detection
    premium_discount_zone_pct: float = 50.0  # Premium zone above 50% of range

SMART_MONEY_CONFIG = SmartMoneyConfig()

# ─────────────────────────────────────────────
# EMA9/20 + VWAP + CPR — JOURNAL-STRICT STRATEGY
# Built to trading_journal.xlsx "My Rules" sheet.
# ─────────────────────────────────────────────
@dataclass
class EMAVWAPCPRConfig:
    # EMA periods (journal rule #2)
    ema_fast: int = 9
    ema_slow: int = 20

    # ATR + SL/T sizing (journal exits a/b/c)
    atr_period: int = 14
    sl_atr_mult: float = 1.5            # hard SL distance
    t1_atr_mult: float = 2.5            # T1 (~1:1.67 RR — within journal's 1:1.5/1:2 range)
    t2_atr_mult: float = 4.0

    # Data + sizing
    min_bars_required: int = 25
    max_risk_per_trade_pct: float = 1.5
    max_lots_per_trade: int = 5

    # RSI sweet zone (journal rule #3)
    rsi_period: int = 14
    rsi_long_min: float = 55.0
    rsi_long_max: float = 65.0
    rsi_short_min: float = 35.0
    rsi_short_max: float = 45.0

    # Strong-body / candle quality (journal rule #5)
    min_body_pct: float = 0.5           # body must be ≥50% of candle range

    # ── V2 features ──
    # RSI extreme partial booking (journal exit #5)
    rsi_extreme_overbought: float = 75.0    # long: ≥75 → book 50%
    rsi_extreme_oversold: float = 25.0      # short: ≤25 → book 50%

    # Breakeven SL move after 1:1 (journal exit #8)
    breakeven_rr_trigger: float = 1.0       # R-multiple before SL moves to entry

    # Partial booking + trailing (journal exits #1, #2)
    partial_qty_pct: float = 0.5            # book 50% at T1

    # Range-day kill (journal kill #2)
    range_day_vwap_crosses: int = 3         # ≥ N VWAP crosses before 10:00 → STOP
    range_day_cutoff_hour: int = 10
    range_day_cutoff_min: int = 0

    # Wide-CPR no-trend kill (journal kill #3)
    wide_cpr_pct_threshold: float = 0.5     # CPR width % of pivot — > this is "wide"
    no_trend_cutoff_hour: int = 10
    no_trend_cutoff_min: int = 30

    # RSI divergence detector (journal exit #6)
    divergence_lookback_bars: int = 15

    # ── Daily higher-timeframe trend filter (DISABLED for V2 paper trading) ──
    # 10-week backtest with this ON: 1 trade (over-filtered for Feb-May 2026
    # range-bound daily regime). V2 (this filter OFF) is the paper trading
    # baseline: 3 trades / 67% WR / +₹13,987 / DD <1% on same window.
    # Re-enable after observing 30+ live sessions if needed.
    require_daily_trend_alignment: bool = False
    daily_ema_fast: int = 9
    daily_ema_slow: int = 20
    daily_lookback_days: int = 365

EMA_VWAP_CPR_CONFIG = EMAVWAPCPRConfig()

# ─────────────────────────────────────────────
# STRATEGY ENABLE/DISABLE FLAGS
# ─────────────────────────────────────────────
ENABLED_STRATEGIES = {
    "option_chain_momentum": True,
    "oi_buildup_trend_follow": True,
    "technical_breakout": True,
    "smart_money_order_block": True,
    "scalping_vwap_bounce": True,
    "price_action_swing": True,
    "ema_vwap_cpr_confluence": True,    # NIFTY/BANKNIFTY 5-min index futures
}

# ─────────────────────────────────────────────
# CANDLE TIMEFRAMES
# ─────────────────────────────────────────────
SCALPING_TF = "1min"
INTRADAY_TF = "5min"
SWING_TF = "15min"
TREND_TF = "60min"
DAILY_TF = "1day"

# ─────────────────────────────────────────────
# LOGGING
# ─────────────────────────────────────────────
LOG_LEVEL = "INFO"
LOG_DIR = "logs"
TRADE_LOG_FILE = "logs/trades.csv"
SIGNAL_LOG_FILE = "logs/signals.csv"
ERROR_LOG_FILE = "logs/errors.log"

# ─────────────────────────────────────────────
# NOTIFICATION SETTINGS
# ─────────────────────────────────────────────
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
NOTIFY_ON_TRADE = True
NOTIFY_ON_SIGNAL = True
NOTIFY_ON_ERROR = True
NOTIFY_DAILY_PNL = True
DAILY_PNL_REPORT_TIME = "15:35"
