#!/usr/bin/env bash
#
# start_day.sh — daily morning bootstrap for the EMA9/20 + VWAP + CPR
# journal-strict trading engine.
#
# Workflow:
#   1. Refresh the Dhan access token (it expires every 24h)
#   2. Launch the dashboard in the background on http://localhost:8080
#   3. Open the dashboard in your default browser
#   4. Start the engine in the foreground (so Ctrl+C exits cleanly)
#
# When the engine exits (Ctrl+C, error, or natural shutdown), the
# dashboard subprocess is killed automatically via a trap.
#
# Usage (from the trading_system/ folder, ~9:00 AM IST on a trading day):
#   ./start_day.sh
#
# Live mode (after you're satisfied with paper trading for 30+ sessions):
#   ./start_day.sh --live

set -eu
cd "$(dirname "$0")"

echo "─────────────────────────────────────────────"
echo "  AlgoTrader — Daily Bootstrap"
echo "  $(date '+%Y-%m-%d %H:%M %Z')"
echo "─────────────────────────────────────────────"

# ── Step 1: refresh Dhan access token ──
echo
echo "Step 1/3 — Refresh Dhan access token"
echo "  Steps to grab a fresh token:"
echo "    1) Open https://web.dhan.co"
echo "    2) Profile (top-right) → API"
echo "    3) Click 'Generate / Copy Access Token'"
echo "    4) Paste it at the prompt below"
echo

python3 update_token.py

# ── Step 2: launch dashboard in background ──
DASHBOARD_PID=""
echo
echo "Step 2/3 — Starting dashboard on http://localhost:8080"

mkdir -p logs

if lsof -i :8080 -t >/dev/null 2>&1; then
    echo "  ⚠️  Port 8080 already in use — skipping dashboard launch"
    echo "     (kill the existing process and re-run if you want a fresh dashboard)"
else
    python3 dashboard/app.py > logs/dashboard.log 2>&1 &
    DASHBOARD_PID=$!
    sleep 2

    # Verify it actually came up (not just that the process started)
    if curl -sS -o /dev/null -w "%{http_code}" http://localhost:8080/ 2>/dev/null | grep -q "^200$"; then
        echo "  ✅ Dashboard up (PID $DASHBOARD_PID, log: logs/dashboard.log)"
        # Open the default browser to the dashboard
        if command -v open >/dev/null 2>&1; then
            open "http://localhost:8080"
        else
            echo "     Open this URL in your browser: http://localhost:8080"
        fi
    else
        echo "  ❌ Dashboard failed to respond on :8080"
        echo "     Check logs/dashboard.log for the error"
        DASHBOARD_PID=""   # don't try to clean up a broken/dead PID
    fi
fi

# ── Cleanup trap: kill dashboard when engine exits ──
cleanup() {
    if [ -n "${DASHBOARD_PID:-}" ] && kill -0 "$DASHBOARD_PID" 2>/dev/null; then
        echo
        echo "Stopping dashboard (PID $DASHBOARD_PID)..."
        kill "$DASHBOARD_PID" 2>/dev/null || true
    fi
}
trap cleanup EXIT INT TERM

# ── Step 3: start trading engine in foreground ──
echo
echo "Step 3/3 — Starting trading engine"
echo

if [ "${1:-}" = "--live" ]; then
    echo "  ⚠️  LIVE MODE requested — real orders will be placed."
    python3 main.py --live
else
    echo "  Paper-trading mode (default). Run with --live for real orders."
    python3 main.py
fi
