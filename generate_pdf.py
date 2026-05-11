"""
Generate Trading System PDF Guide
Run: python3 generate_pdf.py
"""

from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm, cm
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    HRFlowable, PageBreak, KeepTogether
)
from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_RIGHT
from datetime import datetime
import os

OUTPUT = os.path.join(os.path.dirname(__file__), "AlgoTrader_System_Guide.pdf")

# ── COLORS ──
DARK_BG    = colors.HexColor("#0d1117")
BLUE       = colors.HexColor("#58a6ff")
GREEN      = colors.HexColor("#3fb950")
RED        = colors.HexColor("#f85149")
YELLOW     = colors.HexColor("#d29922")
LIGHT_GREY = colors.HexColor("#8b949e")
DARK_PANEL = colors.HexColor("#161b22")
BORDER     = colors.HexColor("#30363d")
WHITE      = colors.HexColor("#e6edf3")
ORANGE     = colors.HexColor("#f0883e")

# ── STYLES ──
styles = getSampleStyleSheet()

def S(name, **kw):
    return ParagraphStyle(name, **kw)

TITLE = S("MyTitle",
    fontSize=28, textColor=BLUE, spaceAfter=4,
    fontName="Helvetica-Bold", alignment=TA_CENTER)

SUBTITLE = S("MySub",
    fontSize=13, textColor=LIGHT_GREY, spaceAfter=2,
    fontName="Helvetica", alignment=TA_CENTER)

H1 = S("H1",
    fontSize=16, textColor=BLUE, spaceBefore=14, spaceAfter=6,
    fontName="Helvetica-Bold", borderPad=4)

H2 = S("H2",
    fontSize=12, textColor=GREEN, spaceBefore=10, spaceAfter=4,
    fontName="Helvetica-Bold")

H3 = S("H3",
    fontSize=10, textColor=ORANGE, spaceBefore=8, spaceAfter=3,
    fontName="Helvetica-Bold")

BODY = S("Body",
    fontSize=9.5, textColor=colors.HexColor("#c9d1d9"),
    spaceAfter=5, fontName="Helvetica", leading=15)

BULLET = S("Bullet",
    fontSize=9.5, textColor=colors.HexColor("#c9d1d9"),
    spaceAfter=3, fontName="Helvetica", leading=14,
    leftIndent=14, bulletIndent=4)

CODE = S("Code",
    fontSize=8.5, textColor=GREEN,
    spaceAfter=4, fontName="Courier", leading=13,
    leftIndent=10, backColor=colors.HexColor("#0d1117"))

NOTE = S("Note",
    fontSize=9, textColor=YELLOW,
    spaceAfter=4, fontName="Helvetica-Oblique", leftIndent=10)

CAPTION = S("Caption",
    fontSize=8, textColor=LIGHT_GREY,
    spaceAfter=2, fontName="Helvetica", alignment=TA_CENTER)

def hr():
    return HRFlowable(width="100%", thickness=0.5, color=BORDER, spaceAfter=8, spaceBefore=4)

def space(h=6):
    return Spacer(1, h)

def bullet(text):
    return Paragraph(f"• {text}", BULLET)

def code(text):
    return Paragraph(text, CODE)

def info_table(rows, col_widths=None):
    """Generic styled table."""
    if not col_widths:
        col_widths = [A4[0] * 0.3, A4[0] * 0.6]
    t = Table(rows, colWidths=col_widths)
    t.setStyle(TableStyle([
        ("BACKGROUND",  (0, 0), (-1, 0),  DARK_PANEL),
        ("TEXTCOLOR",   (0, 0), (-1, 0),  BLUE),
        ("FONTNAME",    (0, 0), (-1, 0),  "Helvetica-Bold"),
        ("FONTSIZE",    (0, 0), (-1, -1), 9),
        ("BACKGROUND",  (0, 1), (-1, -1), colors.HexColor("#0d1117")),
        ("TEXTCOLOR",   (0, 1), (0, -1),  GREEN),
        ("TEXTCOLOR",   (1, 1), (1, -1),  WHITE),
        ("FONTNAME",    (0, 1), (-1, -1), "Helvetica"),
        ("GRID",        (0, 0), (-1, -1), 0.4, BORDER),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.HexColor("#161b22"), colors.HexColor("#0d1117")]),
        ("LEFTPADDING",  (0, 0), (-1, -1), 10),
        ("RIGHTPADDING", (0, 0), (-1, -1), 10),
        ("TOPPADDING",   (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING",(0, 0), (-1, -1), 6),
        ("VALIGN",       (0, 0), (-1, -1), "MIDDLE"),
    ]))
    return t


# ─────────────────────────────────────────────
# BUILD PDF
# ─────────────────────────────────────────────

def build():
    doc = SimpleDocTemplate(
        OUTPUT, pagesize=A4,
        leftMargin=18*mm, rightMargin=18*mm,
        topMargin=20*mm, bottomMargin=20*mm,
        title="AlgoTrader — Automated Trading System Guide",
        author="Rahul Kapoor",
    )

    story = []

    # ══ COVER PAGE ══
    story.append(space(40))
    story.append(Paragraph("⚡ AlgoTrader", TITLE))
    story.append(Paragraph("Automated Trading System — NSE India", SUBTITLE))
    story.append(space(4))
    story.append(Paragraph("Built on Dhan API · Options · Futures · Equity", SUBTITLE))
    story.append(space(20))
    story.append(hr())

    cover_data = [
        ["Broker",      "Dhan (dhanhq.co)"],
        ["Exchange",    "NSE — Options, Futures, Equity"],
        ["Strategies",  "Option Chain + SMC + Indicators + Price Action"],
        ["Mode",        "Paper Trading (Switch to Live when ready)"],
        ["Capital",     "₹1,20,000 (configurable)"],
        ["Alerts",      "Telegram Bot — @tradingrockstar_bot"],
        ["Dashboard",   "http://127.0.0.1:8080"],
        ["Version",     f"1.0 — {datetime.now().strftime('%d %b %Y')}"],
    ]
    story.append(info_table(cover_data, [60*mm, 110*mm]))
    story.append(space(20))
    story.append(hr())
    story.append(Paragraph(
        f"Prepared for: <b>Rahul Kapoor</b> &nbsp;|&nbsp; Generated: {datetime.now().strftime('%d %b %Y, %H:%M')}",
        CAPTION))
    story.append(PageBreak())

    # ══ TABLE OF CONTENTS ══
    story.append(Paragraph("Table of Contents", H1))
    story.append(hr())
    toc = [
        ["1.", "System Architecture",           "3"],
        ["2.", "Module Overview",                "4"],
        ["3.", "Strategies Explained",           "5"],
        ["4.", "Risk Management Rules",          "6"],
        ["5.", "Configuration Guide",            "7"],
        ["6.", "Running the System",             "8"],
        ["7.", "Dashboard Guide",                "9"],
        ["8.", "Telegram Alerts",                "9"],
        ["9.", "Monday Morning Checklist",       "10"],
        ["10.", "Fixing Dhan Security IDs",       "10"],
        ["11.", "Troubleshooting",               "11"],
    ]
    toc_table = Table(toc, colWidths=[12*mm, 130*mm, 20*mm])
    toc_table.setStyle(TableStyle([
        ("TEXTCOLOR",   (0, 0), (1, -1), WHITE),
        ("TEXTCOLOR",   (2, 0), (2, -1), LIGHT_GREY),
        ("FONTNAME",    (0, 0), (-1, -1), "Helvetica"),
        ("FONTSIZE",    (0, 0), (-1, -1), 10),
        ("TOPPADDING",  (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING",(0,0), (-1, -1), 5),
        ("LINEBELOW",   (0, 0), (-1, -2), 0.3, BORDER),
        ("ALIGN",       (2, 0), (2, -1), "RIGHT"),
    ]))
    story.append(toc_table)
    story.append(PageBreak())

    # ══ 1. ARCHITECTURE ══
    story.append(Paragraph("1. System Architecture", H1))
    story.append(hr())
    story.append(Paragraph(
        "The system follows a layered pipeline architecture. Each layer has a single responsibility "
        "and passes its output to the next layer.", BODY))
    story.append(space(6))

    arch = [
        ["Layer",           "Module",                   "Responsibility"],
        ["Data Layer",      "dhan_client.py",           "Fetch market data, option chain, place orders via Dhan API"],
        ["Indicators",      "indicators.py",            "Compute EMA, RSI, MACD, Supertrend, ATR, VWAP, Volume"],
        ["Option Chain",    "option_chain_analyzer.py", "PCR, Max Pain, OI Walls, IV Skew, Call/Put Wall signals"],
        ["Smart Money",     "smart_money.py",           "Order Blocks, FVG, BOS, CHoCH, Premium/Discount zones"],
        ["Signal Engine",   "signal_aggregator.py",     "Score all sources (0-100), fire trade when confidence ≥ 60%"],
        ["Risk Manager",    "risk_manager.py",          "Position sizing, daily loss limit, trailing stops, P&L log"],
        ["Order Executor",  "order_executor.py",        "Place entry+SL orders, monitor positions, EOD square-off"],
        ["Orchestrator",    "main.py",                  "Run full loop every 60s during market hours (9:20–3:15)"],
        ["Dashboard",       "dashboard/app.py",         "Live web UI on http://127.0.0.1:8080"],
    ]
    arch_t = Table(arch, colWidths=[32*mm, 40*mm, 96*mm])
    arch_t.setStyle(TableStyle([
        ("BACKGROUND",  (0, 0), (-1, 0),  DARK_PANEL),
        ("TEXTCOLOR",   (0, 0), (-1, 0),  BLUE),
        ("FONTNAME",    (0, 0), (-1, 0),  "Helvetica-Bold"),
        ("FONTSIZE",    (0, 0), (-1, -1), 8.5),
        ("BACKGROUND",  (0, 1), (-1, -1), colors.HexColor("#0d1117")),
        ("TEXTCOLOR",   (0, 1), (0, -1),  GREEN),
        ("TEXTCOLOR",   (1, 1), (1, -1),  ORANGE),
        ("TEXTCOLOR",   (2, 1), (2, -1),  WHITE),
        ("FONTNAME",    (0, 1), (-1, -1), "Helvetica"),
        ("GRID",        (0, 0), (-1, -1), 0.4, BORDER),
        ("ROWBACKGROUNDS", (0,1), (-1,-1), [colors.HexColor("#161b22"), colors.HexColor("#0d1117")]),
        ("LEFTPADDING",  (0,0), (-1,-1), 8),
        ("TOPPADDING",   (0,0), (-1,-1), 5),
        ("BOTTOMPADDING",(0,0), (-1,-1), 5),
        ("VALIGN",       (0,0), (-1,-1), "TOP"),
        ("WORDWRAP",     (2,1), (2,-1), True),
    ]))
    story.append(arch_t)
    story.append(PageBreak())

    # ══ 2. MODULE OVERVIEW ══
    story.append(Paragraph("2. Module Overview", H1))
    story.append(hr())

    modules = [
        ("config/settings.py", "Central configuration file. All risk rules, indicator periods, strategy toggles, "
         "API keys (via .env), Telegram settings, and market hours are defined here. "
         "This is the first file to edit when tuning the system."),
        ("data/dhan_client.py", "Full Dhan REST API v2 wrapper. Covers: LTP, OHLC, historical candles (1/5/15/60 min), "
         "option chain with all Greeks, order placement/modification/cancellation, positions, holdings, fund limits."),
        ("data/indicators.py", "Pure Python indicator engine (no TA-Lib required). "
         "Includes: EMA (9/21/50/200), SMA, WMA, RSI, MACD, Stochastic, CCI, ATR, Bollinger Bands, "
         "Keltner Channel, Supertrend, ADX, VWAP, OBV, VPT, Pivot Points, Swing Highs/Lows."),
        ("data/option_chain_analyzer.py", "Parses Dhan option chain data and computes: PCR (OI & Volume), "
         "Max Pain, Call Wall / Put Wall (OI-based S/R), IV Skew, OI Buildup/Unwinding detection. "
         "Outputs: BUY_CE / BUY_PE / SELL_CE / SELL_PE / WAIT with 0-100 strength score."),
        ("strategies/smart_money.py", "Smart Money Concepts engine. Detects: Bullish/Bearish Order Blocks, "
         "Fair Value Gaps (FVG), Break of Structure (BOS), Change of Character (CHoCH), "
         "Liquidity Sweeps, Premium/Discount zones. Generates BUY/SELL with precise SL & TP levels."),
        ("strategies/signal_aggregator.py", "Combines all signal sources into one final decision. "
         "Scoring: Option Chain (35pts) + Indicators (35pts) + SMC (20pts) + Price Action (10pts). "
         "Trade fires only when score >= 60 AND R:R >= 1.5."),
        ("risk/risk_manager.py", "Enforces all risk rules: max 2% capital per trade, 3% daily loss limit, "
         "5 max positions, trailing stops, position sizing (Fixed%/Kelly/Fixed Lots). "
         "Logs every trade to logs/trades.csv with full entry/exit details."),
        ("execution/order_executor.py", "Places orders via Dhan API: entry (LIMIT/MARKET), SL (STOP_LOSS_MARKET), "
         "target (LIMIT). Monitors all open positions on every tick. Sends Telegram alerts. "
         "Auto squares off all intraday positions before 3:15 PM."),
    ]

    for name, desc in modules:
        story.append(Paragraph(name, H3))
        story.append(Paragraph(desc, BODY))

    story.append(PageBreak())

    # ══ 3. STRATEGIES ══
    story.append(Paragraph("3. Strategies Explained", H1))
    story.append(hr())

    story.append(Paragraph("3.1 Option Chain Analysis", H2))
    story.append(Paragraph(
        "Analyses the full NSE option chain for NIFTY and BANKNIFTY every 60 seconds.", BODY))
    strats_oc = [
        ["Metric",      "What It Tells Us",                         "Signal"],
        ["PCR > 1.2",   "More puts than calls = hedging = support", "Bullish"],
        ["PCR < 0.8",   "More calls = complacency or greed",        "Bearish"],
        ["Call Wall",   "Highest call OI strike = resistance",      "Sell above"],
        ["Put Wall",    "Highest put OI strike = support",          "Buy near"],
        ["Max Pain",    "Where option writers profit most",         "Range target"],
        ["OI Buildup",  "Rising OI + rising price = long buildup",  "Trend follow"],
        ["IV Skew",     "Put IV > Call IV = smart money hedging",   "Caution/Hedge"],
    ]
    t = Table(strats_oc, colWidths=[30*mm, 95*mm, 43*mm])
    t.setStyle(TableStyle([
        ("BACKGROUND",  (0,0), (-1,0),  DARK_PANEL),
        ("TEXTCOLOR",   (0,0), (-1,0),  BLUE),
        ("FONTNAME",    (0,0), (-1,0),  "Helvetica-Bold"),
        ("FONTSIZE",    (0,0), (-1,-1), 8.5),
        ("BACKGROUND",  (0,1), (-1,-1), colors.HexColor("#0d1117")),
        ("TEXTCOLOR",   (0,1), (0,-1),  GREEN),
        ("TEXTCOLOR",   (1,1), (1,-1),  WHITE),
        ("TEXTCOLOR",   (2,1), (2,-1),  YELLOW),
        ("FONTNAME",    (0,1), (-1,-1), "Helvetica"),
        ("GRID",        (0,0), (-1,-1), 0.4, BORDER),
        ("ROWBACKGROUNDS",(0,1),(-1,-1),[colors.HexColor("#161b22"), colors.HexColor("#0d1117")]),
        ("LEFTPADDING", (0,0), (-1,-1), 8),
        ("TOPPADDING",  (0,0), (-1,-1), 5),
        ("BOTTOMPADDING",(0,0),(-1,-1), 5),
    ]))
    story.append(t)
    story.append(space(10))

    story.append(Paragraph("3.2 Technical Indicators", H2))
    ind_rows = [
        ["Indicator",     "Period",   "Signal Condition"],
        ["EMA Stack",     "9/21/50",  "9 > 21 > 50 = Bullish | 9 < 21 < 50 = Bearish"],
        ["RSI",           "14",       "< 30 = Oversold (Buy) | > 70 = Overbought (Sell)"],
        ["MACD",          "12/26/9",  "Histogram cross above 0 = Bullish | below 0 = Bearish"],
        ["Supertrend",    "10, 3.0",  "Price above line = Uptrend | below = Downtrend"],
        ["ADX",           "14",       "> 25 = Strong trend | < 20 = Weak / Range"],
        ["Bollinger",     "20, 2",    "Price at lower band = Oversold | upper band = Overbought"],
        ["VWAP",          "Intraday", "Price above VWAP = Bullish | below = Bearish"],
        ["Volume",        "20 MA",    "Spike > 2x average = Confirms breakout/breakdown"],
    ]
    t2 = Table(ind_rows, colWidths=[35*mm, 22*mm, 111*mm])
    t2.setStyle(TableStyle([
        ("BACKGROUND",  (0,0), (-1,0),  DARK_PANEL),
        ("TEXTCOLOR",   (0,0), (-1,0),  BLUE),
        ("FONTNAME",    (0,0), (-1,0),  "Helvetica-Bold"),
        ("FONTSIZE",    (0,0), (-1,-1), 8.5),
        ("BACKGROUND",  (0,1), (-1,-1), colors.HexColor("#0d1117")),
        ("TEXTCOLOR",   (0,1), (0,-1),  GREEN),
        ("TEXTCOLOR",   (1,1), (1,-1),  LIGHT_GREY),
        ("TEXTCOLOR",   (2,1), (2,-1),  WHITE),
        ("FONTNAME",    (0,1), (-1,-1), "Helvetica"),
        ("GRID",        (0,0), (-1,-1), 0.4, BORDER),
        ("ROWBACKGROUNDS",(0,1),(-1,-1),[colors.HexColor("#161b22"), colors.HexColor("#0d1117")]),
        ("LEFTPADDING", (0,0), (-1,-1), 8),
        ("TOPPADDING",  (0,0), (-1,-1), 5),
        ("BOTTOMPADDING",(0,0),(-1,-1), 5),
    ]))
    story.append(t2)
    story.append(space(10))

    story.append(Paragraph("3.3 Smart Money Concepts (SMC)", H2))
    smc_items = [
        ("Order Block (OB)", "Last bearish candle before a bullish impulse (Bullish OB) or last bullish candle "
         "before a bearish impulse (Bearish OB). Price returning to OB = high probability entry."),
        ("Fair Value Gap (FVG)", "3-candle imbalance where middle candle leaves a gap. Price tends to "
         "return to fill the gap. Minimum gap size: 0.1% of price."),
        ("Break of Structure (BOS)", "Continuation signal — price breaks the last swing high (bullish BOS) "
         "or last swing low (bearish BOS). Confirmed after 2 candles."),
        ("Change of Character (CHoCH)", "Reversal signal — price breaks against the current structure. "
         "Signals a potential trend change. Wait for confirmation before trading."),
        ("Premium/Discount", "Premium zone = top 50% of swing range (sell area). "
         "Discount zone = bottom 50% (buy area). Best entries in discount for buys, premium for sells."),
    ]
    for title, desc in smc_items:
        story.append(Paragraph(f"<b>{title}:</b> {desc}", BODY))

    story.append(PageBreak())

    # ══ 4. RISK MANAGEMENT ══
    story.append(Paragraph("4. Risk Management Rules", H1))
    story.append(hr())

    risk_rows = [
        ["Rule",                    "Setting",  "What Happens When Triggered"],
        ["Max Capital Per Trade",   "2%",       "Max ₹2,400 at risk per trade (on ₹1.2L capital)"],
        ["Daily Loss Limit",        "3%",       "Trading stops for the day if P&L drops by ₹3,600"],
        ["Max Open Positions",      "5",        "No new trades until an existing one closes"],
        ["Max Positions / Symbol",  "2",        "Prevents over-concentration in one stock"],
        ["Max Trades Per Day",      "20",       "Prevents overtrading / revenge trading"],
        ["Min Risk:Reward",         "1.5x",     "Trade rejected if target < 1.5x the risk"],
        ["Trailing Stop Trigger",   "+1.5%",    "Trailing stop activates after 1.5% profit"],
        ["Trailing Stop Distance",  "0.75%",    "Stop trails 0.75% below the highest price reached"],
        ["EOD Square Off",          "3:15 PM",  "All F&O positions closed automatically before expiry"],
    ]
    risk_t = Table(risk_rows, colWidths=[52*mm, 22*mm, 94*mm])
    risk_t.setStyle(TableStyle([
        ("BACKGROUND",  (0,0), (-1,0),  DARK_PANEL),
        ("TEXTCOLOR",   (0,0), (-1,0),  BLUE),
        ("FONTNAME",    (0,0), (-1,0),  "Helvetica-Bold"),
        ("FONTSIZE",    (0,0), (-1,-1), 8.5),
        ("BACKGROUND",  (0,1), (-1,-1), colors.HexColor("#0d1117")),
        ("TEXTCOLOR",   (0,1), (0,-1),  GREEN),
        ("TEXTCOLOR",   (1,1), (1,-1),  YELLOW),
        ("TEXTCOLOR",   (2,1), (2,-1),  WHITE),
        ("FONTNAME",    (0,1), (-1,-1), "Helvetica"),
        ("GRID",        (0,0), (-1,-1), 0.4, BORDER),
        ("ROWBACKGROUNDS",(0,1),(-1,-1),[colors.HexColor("#161b22"), colors.HexColor("#0d1117")]),
        ("LEFTPADDING", (0,0), (-1,-1), 8),
        ("TOPPADDING",  (0,0), (-1,-1), 5),
        ("BOTTOMPADDING",(0,0),(-1,-1), 5),
    ]))
    story.append(risk_t)
    story.append(space(8))
    story.append(Paragraph(
        "⚠ All risk settings can be changed in config/settings.py under the RiskConfig dataclass. "
        "It is strongly recommended to run in paper trading mode for at least 2 weeks before going live.", NOTE))
    story.append(PageBreak())

    # ══ 5. CONFIGURATION GUIDE ══
    story.append(Paragraph("5. Configuration Guide", H1))
    story.append(hr())
    story.append(Paragraph("5.1 .env File (Credentials)", H2))
    story.append(Paragraph("Located in the trading_system/ folder. Never share this file.", BODY))
    story.append(code("DHAN_CLIENT_ID=your_client_id"))
    story.append(code("DHAN_ACCESS_TOKEN=your_access_token"))
    story.append(code("PAPER_TRADING=true"))
    story.append(code("TELEGRAM_BOT_TOKEN=your_bot_token"))
    story.append(code("TELEGRAM_CHAT_ID=your_chat_id"))
    story.append(space(6))

    story.append(Paragraph("5.2 Key Settings in config/settings.py", H2))
    cfg_rows = [
        ["Setting",                     "Default",  "Description"],
        ["PAPER_TRADING",               "true",     "Set to false only when ready for live trading"],
        ["max_capital_per_trade_pct",   "2.0%",     "% of total capital at risk per trade"],
        ["max_daily_loss_pct",          "3.0%",     "Stop trading if daily loss exceeds this %"],
        ["max_open_positions",          "5",        "Maximum simultaneous open trades"],
        ["risk_reward_min",             "1.5",      "Minimum R:R before a trade is taken"],
        ["trailing_stop_enabled",       "True",     "Enable/disable trailing stop"],
        ["pcr_bullish_threshold",       "1.2",      "PCR above this = bullish signal"],
        ["pcr_bearish_threshold",       "0.8",      "PCR below this = bearish signal"],
        ["SIGNAL_THRESHOLD",            "60",       "Min confidence score (0-100) to fire a trade"],
        ["AVOID_TRADE_NEAR_OPEN_MINS",  "5",        "Skip trading in first 5 min after open"],
        ["AVOID_TRADE_NEAR_CLOSE_MINS", "15",       "Stop new trades 15 min before market close"],
    ]
    cfg_t = Table(cfg_rows, colWidths=[60*mm, 22*mm, 86*mm])
    cfg_t.setStyle(TableStyle([
        ("BACKGROUND",  (0,0), (-1,0),  DARK_PANEL),
        ("TEXTCOLOR",   (0,0), (-1,0),  BLUE),
        ("FONTNAME",    (0,0), (-1,0),  "Helvetica-Bold"),
        ("FONTSIZE",    (0,0), (-1,-1), 8.5),
        ("BACKGROUND",  (0,1), (-1,-1), colors.HexColor("#0d1117")),
        ("TEXTCOLOR",   (0,1), (0,-1),  GREEN),
        ("TEXTCOLOR",   (1,1), (1,-1),  YELLOW),
        ("TEXTCOLOR",   (2,1), (2,-1),  WHITE),
        ("FONTNAME",    (0,1), (-1,-1), "Helvetica"),
        ("GRID",        (0,0), (-1,-1), 0.4, BORDER),
        ("ROWBACKGROUNDS",(0,1),(-1,-1),[colors.HexColor("#161b22"), colors.HexColor("#0d1117")]),
        ("LEFTPADDING", (0,0), (-1,-1), 8),
        ("TOPPADDING",  (0,0), (-1,-1), 5),
        ("BOTTOMPADDING",(0,0),(-1,-1), 5),
    ]))
    story.append(cfg_t)
    story.append(PageBreak())

    # ══ 6. RUNNING THE SYSTEM ══
    story.append(Paragraph("6. Running the System", H1))
    story.append(hr())

    story.append(Paragraph("Step 1 — Install Dependencies", H2))
    story.append(code("/Library/Developer/CommandLineTools/usr/bin/python3 -m pip install -r requirements.txt"))
    story.append(space(4))

    story.append(Paragraph("Step 2 — Test Connections", H2))
    story.append(code("/Library/Developer/CommandLineTools/usr/bin/python3 check_connection.py"))
    story.append(code("/Library/Developer/CommandLineTools/usr/bin/python3 test_telegram.py"))
    story.append(space(4))

    story.append(Paragraph("Step 3 — Run Paper Trading (Default)", H2))
    story.append(code("/Library/Developer/CommandLineTools/usr/bin/python3 main.py"))
    story.append(Paragraph("Starts the full trading loop. Runs 9:20 AM – 3:15 PM IST on weekdays.", BODY))
    story.append(space(4))

    story.append(Paragraph("Step 4 — Open Dashboard", H2))
    story.append(code("/Library/Developer/CommandLineTools/usr/bin/python3 dashboard/app.py"))
    story.append(Paragraph("Open Chrome → http://127.0.0.1:8080", BODY))
    story.append(space(4))

    story.append(Paragraph("Step 5 — Go Live (When Ready)", H2))
    story.append(code("/Library/Developer/CommandLineTools/usr/bin/python3 main.py --live"))
    story.append(Paragraph(
        "⚠ This will place REAL orders on your Dhan account. "
        "Type CONFIRM when prompted. Ensure paper trading has been profitable for 2+ weeks first.", NOTE))
    story.append(space(4))

    story.append(Paragraph("Stop the Engine", H2))
    story.append(Paragraph("Press Ctrl+C in the terminal. The engine will square off all open positions automatically.", BODY))
    story.append(PageBreak())

    # ══ 7. DASHBOARD ══
    story.append(Paragraph("7. Dashboard Guide", H1))
    story.append(hr())
    story.append(Paragraph("URL: http://127.0.0.1:8080 (auto-refreshes every 30 seconds)", BODY))
    story.append(space(4))
    dash_rows = [
        ["Panel",               "Shows"],
        ["Today's P&L",         "Net profit/loss for the session in ₹ and %"],
        ["Open Positions",       "All live trades with entry, LTP, unrealised P&L, SL"],
        ["Trades Today",         "Count of total, closed trades and daily trade limit"],
        ["Win Rate",             "Winners vs Losers with percentage"],
        ["Available Capital",    "Remaining deployable capital with visual bar"],
        ["Daily Loss Meter",     "How much of the 3% daily loss limit has been used"],
        ["System Status",        "Dhan API, Telegram, Risk Manager, active strategies"],
        ["Recent Signals",       "Last 10 signals with confidence bars and R:R"],
        ["Trade History",        "Last 20 closed trades with entry, exit, P&L, status"],
    ]
    story.append(info_table(dash_rows, [50*mm, 118*mm]))
    story.append(space(10))

    # ══ 8. TELEGRAM ══
    story.append(Paragraph("8. Telegram Alerts", H1))
    story.append(hr())
    story.append(Paragraph("Bot: @tradingrockstar_bot — You receive alerts for:", BODY))
    tg_items = [
        "✅ Trade Entry — Symbol, type, entry price, SL, target, confidence, R:R",
        "🔴 Stop Loss Hit — Exit price, P&L, reason",
        "🎯 Target Hit — Exit price, profit amount",
        "📈 Trailing Stop Update — New stop level",
        "⏰ EOD Square Off — All positions closed with summary",
        "📊 Daily Summary — Total trades, win rate, net P&L",
        "⚠ Engine Errors — Any critical errors during runtime",
    ]
    for item in tg_items:
        story.append(bullet(item))
    story.append(PageBreak())

    # ══ 9. MONDAY CHECKLIST ══
    story.append(Paragraph("9. Monday Morning Checklist", H1))
    story.append(hr())
    checklist = [
        ["☐", "Ensure internet connection is stable"],
        ["☐", "Open terminal in trading_system/ folder"],
        ["☐", "Run: python3 main.py (starts at 9:15 AM)"],
        ["☐", "Open Chrome → http://127.0.0.1:8080 for dashboard"],
        ["☐", "Open Telegram → watch for @tradingrockstar_bot alerts"],
        ["☐", "Monitor first 30 minutes manually to verify signals"],
        ["☐", "Check logs/trading_engine.log if anything looks wrong"],
        ["☐", "Do NOT interrupt the process during market hours"],
        ["☐", "After market close — review logs/trades.csv for results"],
    ]
    cl_t = Table(checklist, colWidths=[10*mm, 158*mm])
    cl_t.setStyle(TableStyle([
        ("TEXTCOLOR",   (0,0), (0,-1), GREEN),
        ("TEXTCOLOR",   (1,0), (1,-1), WHITE),
        ("FONTNAME",    (0,0), (-1,-1), "Helvetica"),
        ("FONTSIZE",    (0,0), (-1,-1), 10),
        ("TOPPADDING",  (0,0), (-1,-1), 5),
        ("BOTTOMPADDING",(0,0),(-1,-1), 5),
        ("LINEBELOW",   (0,0), (-1,-2), 0.3, BORDER),
    ]))
    story.append(cl_t)
    story.append(space(10))

    # ══ 10. SECURITY IDs ══
    story.append(Paragraph("10. Fixing Dhan Security IDs", H1))
    story.append(hr())
    story.append(Paragraph(
        "The instrument security IDs in main.py need to match Dhan's official instrument list "
        "for market data to work correctly. Follow these steps:", BODY))
    steps = [
        "Log in to dhanhq.co → API section → Download Instruments CSV",
        "Open the CSV and search for NIFTY, BANKNIFTY, RELIANCE, HDFCBANK, INFY etc.",
        "Copy the SEM_SMST_SECURITY_ID value for each symbol",
        "Open trading_system/main.py → find INSTRUMENT_REGISTRY",
        'Update each "security_id" value with the correct ID from the CSV',
        "Save the file and restart main.py",
    ]
    for i, step in enumerate(steps, 1):
        story.append(Paragraph(f"{i}. {step}", BODY))
    story.append(PageBreak())

    # ══ 11. TROUBLESHOOTING ══
    story.append(Paragraph("11. Troubleshooting", H1))
    story.append(hr())
    trouble = [
        ["Error / Issue",                   "Fix"],
        ["pip: command not found",           "Use: python3 -m pip install -r requirements.txt"],
        ["Port 5000 already in use",         "Dashboard uses port 8080 now. Or disable AirPlay in System Settings"],
        ["API returns 401",                  "Regenerate Access Token on dhanhq.co — tokens expire"],
        ["API returns 400 Invalid Request",  "Update security IDs in main.py from Dhan instruments CSV"],
        ["No data returned for symbol",      "Check security_id is correct and Data API subscription is active"],
        ["Telegram message not received",    "Send /start to your bot first, then run test_telegram.py again"],
        ["Engine not trading at 9:15",       "Trading starts at 9:20 (skips first 5 min). Check logs/ folder"],
        ["ModuleNotFoundError",              "Re-run: python3 -m pip install -r requirements.txt"],
        ["Daily loss limit hit",             "Normal — trading stops for the day. Resumes next trading day"],
        ["Signal shows WAIT always",         "Confidence threshold is 60%. Lower it in signal_aggregator.py if needed"],
    ]
    tr_t = Table(trouble, colWidths=[70*mm, 98*mm])
    tr_t.setStyle(TableStyle([
        ("BACKGROUND",  (0,0), (-1,0),  DARK_PANEL),
        ("TEXTCOLOR",   (0,0), (-1,0),  BLUE),
        ("FONTNAME",    (0,0), (-1,0),  "Helvetica-Bold"),
        ("FONTSIZE",    (0,0), (-1,-1), 8.5),
        ("BACKGROUND",  (0,1), (-1,-1), colors.HexColor("#0d1117")),
        ("TEXTCOLOR",   (0,1), (0,-1),  RED),
        ("TEXTCOLOR",   (1,1), (1,-1),  WHITE),
        ("FONTNAME",    (0,1), (-1,-1), "Helvetica"),
        ("GRID",        (0,0), (-1,-1), 0.4, BORDER),
        ("ROWBACKGROUNDS",(0,1),(-1,-1),[colors.HexColor("#161b22"), colors.HexColor("#0d1117")]),
        ("LEFTPADDING", (0,0), (-1,-1), 8),
        ("TOPPADDING",  (0,0), (-1,-1), 5),
        ("BOTTOMPADDING",(0,0),(-1,-1), 5),
        ("VALIGN",      (0,0), (-1,-1), "TOP"),
    ]))
    story.append(tr_t)
    story.append(space(16))
    story.append(hr())
    story.append(Paragraph(
        f"AlgoTrader v1.0 — Built for Rahul Kapoor &nbsp;|&nbsp; "
        f"Dhan API + NSE India &nbsp;|&nbsp; {datetime.now().strftime('%d %b %Y')}",
        CAPTION))

    doc.build(story)
    print(f"\n✅ PDF created: {OUTPUT}\n")


if __name__ == "__main__":
    build()
