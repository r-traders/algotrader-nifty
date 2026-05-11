#!/usr/bin/env bash
#
# start_day.sh — daily morning bootstrap for the EMA9/20 + VWAP + CPR
# journal-strict trading engine.
#
# Workflow:
#   1. Refresh the Dhan access token (it expires every 24h)
#   2. If the token validates, start the engine in paper mode
#
# Usage (from the trading_system/ folder, ~9:00 AM IST on a trading day):
#   ./start_day.sh
#
# Live mode (after you're satisfied with paper trading for 30+ sessions):
#   ./start_day.sh --live

set -e
cd "$(dirname "$0")"

echo "─────────────────────────────────────────────"
echo "  AlgoTrader — Daily Bootstrap"
echo "  $(date '+%Y-%m-%d %H:%M %Z')"
echo "─────────────────────────────────────────────"
echo
echo "Step 1/2 — Refresh Dhan access token"
echo "  Steps to grab a fresh token:"
echo "    1) Open https://web.dhan.co"
echo "    2) Profile (top-right) → API"
echo "    3) Click 'Generate / Copy Access Token'"
echo "    4) Paste it at the prompt below"
echo

python3 update_token.py

echo
echo "Step 2/2 — Starting trading engine"
echo

if [ "$1" = "--live" ]; then
    echo "  ⚠️  LIVE MODE requested — real orders will be placed."
    python3 main.py --live
else
    echo "  Paper-trading mode (default). Run with --live for real orders."
    python3 main.py
fi
