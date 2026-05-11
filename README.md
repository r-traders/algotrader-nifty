# AlgoTrader — NIFTY/BANKNIFTY 5-min Journal Strategy

A Python trading engine for the Indian index-futures market (Dhan broker).
Implements the EMA9/20 + VWAP + CPR confluence strategy strictly per the
trading journal's "My Rules" sheet, with full risk management and live
position monitoring.

> **Status: paper trading only.** Live trading requires the explicit `--live`
> flag plus a typed `CONFIRM` prompt. Read every section before using live.

---

## Daily workflow (the only thing you need to remember)

Every trading morning, ~09:00 IST:

```bash
cd trading_system/
./start_day.sh
```

That script:

1. Prompts you to paste a fresh Dhan access token (Dhan tokens expire every 24h)
2. Verifies the token against `api.dhan.co/v2/fundlimit` and writes it to `.env`
3. Starts the trading engine in **paper mode**

To grab the token: open [web.dhan.co](https://web.dhan.co) → Profile → API →
*Generate / Copy Access Token*.

To go live (only after 30+ profitable paper sessions):

```bash
./start_day.sh --live
```

You'll get a `CONFIRM` prompt — type that literal string to proceed.

---

## First-time setup

```bash
cd trading_system/

# 1. Copy the env template and fill in your credentials
cp .env.example .env
#   Edit .env:
#     DHAN_CLIENT_ID    = your Dhan client id
#     DHAN_ACCESS_TOKEN = (will be set by update_token.py daily)
#     PAPER_TRADING     = true
#     TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID — optional for alerts

# 2. Install Python deps
pip3 install -r requirements.txt

# 3. Verify Dhan instrument IDs are current (run monthly after expiry rollover)
python3 find_futures_ids.py
```

---

## Strategy rules (verbatim from trading journal)

**Entries — all 8 must be YES on a CLOSED 5-min bar:**

1. Price above VWAP (long) / below VWAP (short)
2. EMA 9 crossed above/below EMA 20 on this just-closed bar
3. RSI 55–65 (long) / 35–45 (short)
4. Volume on crossover candle above 20-period avg
5. Strong body (≥ 50% of candle range) — no doji/spinning top
6. CPR bias agrees with direction
7. Time inside 9:45–11:30 IST or 13:30–14:30 IST
8. VWAP and EMA agree (implied by 1+2)

**Exits — any of:**

- Hard SL: 1.5×ATR
- Target 1 (partial 50%): 2.5×ATR
- Trailing remaining 50% by EMA 9 after T1
- Move SL to breakeven after 1:1 move
- EMA 9 crosses back against the trade → close all
- Price crosses VWAP against the trade → close all
- RSI 75+/25- → book 50%
- RSI divergence (price new high, RSI lower high — or mirror) → close all
- EOD square-off at 15:15

**Kill switches — stop trading the day if any fire:**

- 2 consecutive SL hits today
- Price crossed VWAP 3+ times by 10:00 AM (range day)
- After 10:30 AM with wide CPR + price still inside CPR (no-trend day)

---

## Project structure

```
trading_system/
├── start_day.sh                # Daily bootstrap (refresh token + start engine)
├── main.py                     # Live engine orchestrator
├── update_token.py             # Refresh Dhan access token
├── requirements.txt
├── .env.example                # Credential template (.env is .gitignored)
│
├── config/settings.py          # All tunables in one file
├── data/                       # Dhan client + indicators + option chain
├── strategies/
│   ├── ema_vwap_cpr.py         # The journal-strict strategy
│   ├── signal_aggregator.py    # Legacy multi-source aggregator
│   └── smart_money.py          # SMC analysis (used by aggregator)
├── risk/risk_manager.py        # Position sizing, daily loss guards
├── execution/order_executor.py # Order placement + position monitoring
├── dashboard/app.py            # Optional live dashboard (Flask)
├── backtest/
│   ├── ema_strategy_backtester.py    # Backtester for the new strategy
│   ├── run_ema_vwap_cpr.py           # Runner CLI
│   └── engine.py                     # Legacy backtester (multi-strategy)
└── logs/                       # Runtime logs (gitignored)
```

---

## Backtesting

```bash
# Recent 6 weeks (uses JUN2026 contracts)
python3 backtest/run_ema_vwap_cpr.py --start 2026-04-01 --end 2026-05-09

# 10 weeks (uses MAY2026 contracts — more history)
python3 backtest/run_ema_vwap_cpr.py \
    --start 2026-02-25 --end 2026-05-09 \
    --contract-month may
```

⚠️ **Don't run backtests right before market open.** Sequential historical
API calls can trip Dhan's rate limit and revoke the access token. The
backtester sleeps 1.5s between chunks, but if you're paranoid, run on
weekends only.

Outputs land in `backtest_results/`: `ema_strategy_trades.csv`,
`ema_strategy_summary.csv`, `ema_strategy_equity.json`.

---

## Configuration

All tunables live in [`config/settings.py`](config/settings.py) in the
`EMAVWAPCPRConfig` dataclass. Key knobs:

- `sl_atr_mult`, `t1_atr_mult`, `t2_atr_mult` — risk sizing in ATR units
- `max_risk_per_trade_pct` — capital risked per trade (default 1.5%)
- `max_lots_per_trade` — lot cap regardless of risk budget
- `rsi_long_min/max`, `rsi_short_min/max` — entry sweet zones
- `rsi_extreme_overbought/oversold` — partial-booking triggers (75/25)
- `wide_cpr_pct_threshold` — CPR width % cutoff for no-trend kill switch
- `range_day_vwap_crosses` — VWAP-crosses threshold for range-day kill
- `require_daily_trend_alignment` — V3 daily filter (currently OFF; was too
  restrictive on Feb–May 2026 range-bound regime)

Lot sizes and instrument IDs live in `INSTRUMENT_REGISTRY` at the top of
[`main.py`](main.py). Update monthly after expiry rollover.

---

## Safety checklist before going live

- [ ] Run paper mode for 30+ trading sessions
- [ ] Verify NIFTY/BANKNIFTY security IDs are current (`find_futures_ids.py`)
- [ ] Verify lot sizes match current SEBI revision
- [ ] Confirm available margin in your Dhan account ≥ `max_lots_per_trade × lot_size × index_value × 0.2` (rough NRML margin)
- [ ] Set `PAPER_TRADING=false` in `.env` OR pass `--live` to `start_day.sh`
- [ ] Telegram alerts configured (so you see fills + errors on phone)
- [ ] Process supervisor (systemd / launchd / pm2) wraps `main.py` for auto-restart
- [ ] You've read the strategy rules and accept the risk

---

## Token rotation hygiene

The Dhan access token is a JWT that grants full trading access to your
account. Treat it like a password.

- Token expires every 24 hours — refresh daily via `update_token.py` (or `start_day.sh`)
- If you ever share or paste the token (e.g. in chat with someone helping you debug), **revoke and regenerate** at web.dhan.co → Profile → API
- Never commit `.env` to git — it's in `.gitignore` but double-check before pushing
