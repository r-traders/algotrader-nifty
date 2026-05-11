"""
Full Dhan API Test — tests all instruments in INSTRUMENT_REGISTRY
Run: python3 test_api.py
"""

import os, sys, time, requests, datetime
from dotenv import load_dotenv
load_dotenv()

CLIENT_ID    = os.getenv("DHAN_CLIENT_ID", "")
ACCESS_TOKEN = os.getenv("DHAN_ACCESS_TOKEN", "")
HEADERS = {
    "access-token":  ACCESS_TOKEN,
    "client-id":     CLIENT_ID,
    "Content-Type":  "application/json",
}

# All instruments — security IDs confirmed from Dhan api-scrip-master.csv
INSTRUMENTS = {
    "NIFTY":       {"security_id": "13",    "exchange": "NSE_IDX", "instrument": "INDEX"},
    "BANKNIFTY":   {"security_id": "25",    "exchange": "NSE_IDX", "instrument": "INDEX"},
    "MIDCAPNIFTY": {"security_id": "11914", "exchange": "NSE_IDX", "instrument": "INDEX"},
    "RELIANCE":    {"security_id": "2885",  "exchange": "NSE_EQ",  "instrument": "EQUITY"},
    "TCS":         {"security_id": "11536", "exchange": "NSE_EQ",  "instrument": "EQUITY"},
    "HDFCBANK":    {"security_id": "1333",  "exchange": "NSE_EQ",  "instrument": "EQUITY"},
    "INFY":        {"security_id": "1594",  "exchange": "NSE_EQ",  "instrument": "EQUITY"},
    "ICICIBANK":   {"security_id": "4963",  "exchange": "NSE_EQ",  "instrument": "EQUITY"},
    "SBIN":        {"security_id": "3045",  "exchange": "NSE_EQ",  "instrument": "EQUITY"},
}

# NOTE: Dhan LTP API requires integer security IDs for NSE_EQ (not strings)
# Group equities in one call to avoid rate limits

print("\n" + "="*60)
print("  🔌  FULL DHAN API TEST — All Instruments")
print("="*60)

# ── 1. Credentials ──
print(f"\n1️⃣  Credentials:")
print(f"   Client ID    : {'✅ ' + CLIENT_ID[:8] + '***' if CLIENT_ID else '❌ MISSING'}")
print(f"   Access Token : {'✅ ' + ACCESS_TOKEN[:12] + '...' if ACCESS_TOKEN else '❌ MISSING'}")
if not CLIENT_ID or not ACCESS_TOKEN:
    print("\n❌ Credentials missing in .env — cannot proceed.")
    sys.exit(1)

# ── 2. Auth check ──
print(f"\n2️⃣  Authentication & Funds:")
try:
    r = requests.get("https://api.dhan.co/v2/fundlimit", headers=HEADERS, timeout=10)
    if r.status_code == 200:
        bal = r.json().get("availabelBalance", r.json().get("availableBalance", "N/A"))
        print(f"   ✅ Auth OK  |  Available Funds: ₹{bal}")
    elif r.status_code == 401:
        print("   ❌ Token EXPIRED — run: python3 update_token.py")
        sys.exit(1)
    else:
        print(f"   ⚠️  Status {r.status_code}: {r.text[:100]}")
except Exception as e:
    print(f"   ❌ {e}")
    sys.exit(1)

# ── 3. LTP test (group by exchange to avoid rate limits) ──
print(f"\n3️⃣  LTP Market Data (1-sec delay between calls):")
ltp_results = {}

# Test indices first (NSE_IDX) — one call
idx_payload = {"NSE_IDX": [int(v["security_id"]) for v in INSTRUMENTS.values() if v["exchange"] == "NSE_IDX"]}
idx_syms    = {v["security_id"]: k for k, v in INSTRUMENTS.items() if v["exchange"] == "NSE_IDX"}
try:
    r = requests.post("https://api.dhan.co/v2/marketfeed/ltp",
                      headers=HEADERS, json=idx_payload, timeout=10)
    data = r.json()
    if r.status_code == 200 and data.get("status") != "failed":
        for sec_id, val in data.get("data", {}).items():
            sym = idx_syms.get(sec_id, sec_id)
            ltp = val.get("last_price") if isinstance(val, dict) else None
            if ltp:
                print(f"   ✅ {sym:14} LTP: ₹{ltp:,.2f}")
            else:
                print(f"   ⚠️  {sym:14} OK (markets closed — LTP empty on weekends)")
            ltp_results[sym] = True
    else:
        print(f"   ❌ Indices batch  →  {str(data)[:100]}")
        for sym in [k for k,v in INSTRUMENTS.items() if v["exchange"]=="NSE_IDX"]:
            ltp_results[sym] = False
except Exception as e:
    print(f"   ❌ Indices error: {e}")

time.sleep(1)

# Test equities (NSE_EQ) — one batch call with INTEGER security IDs
eq_payload = {"NSE_EQ": [int(v["security_id"]) for v in INSTRUMENTS.values() if v["exchange"] == "NSE_EQ"]}
eq_syms    = {v["security_id"]: k for k, v in INSTRUMENTS.items() if v["exchange"] == "NSE_EQ"}
try:
    r = requests.post("https://api.dhan.co/v2/marketfeed/ltp",
                      headers=HEADERS, json=eq_payload, timeout=10)
    data = r.json()
    if r.status_code == 200 and data.get("status") != "failed":
        for sec_id, val in data.get("data", {}).items():
            sym = eq_syms.get(str(sec_id), sec_id)
            ltp = val.get("last_price") if isinstance(val, dict) else None
            if ltp:
                print(f"   ✅ {sym:14} LTP: ₹{ltp:,.2f}")
            else:
                print(f"   ⚠️  {sym:14} OK (markets closed — LTP empty on weekends)")
            ltp_results[sym] = True
    else:
        print(f"   ❌ Equities batch →  {str(data)[:100]}")
        for sym in [k for k,v in INSTRUMENTS.items() if v["exchange"]=="NSE_EQ"]:
            ltp_results[sym] = False
except Exception as e:
    print(f"   ❌ Equities error: {e}")

time.sleep(1)

# ── 4. Historical candle test (NIFTY + RELIANCE) ──
print(f"\n4️⃣  Historical Candle Data (15-min, last 5 trading days):")
end_dt   = datetime.date.today()
start_dt = end_dt - datetime.timedelta(days=10)
candle_tests = [
    ("NIFTY",    "13",   "NSE_IDX", "INDEX"),
    ("RELIANCE", "2885", "NSE_EQ",  "EQUITY"),
]
for sym, sec_id, exch, instr in candle_tests:
    try:
        time.sleep(1)
        payload = {
            "securityId":      sec_id,
            "exchangeSegment": exch,
            "instrument":      instr,
            "interval":        15,       # integer, not string
            "expiryCode":      0,
            "oi":              False,
            "fromDate":        start_dt.strftime("%Y-%m-%d"),
            "toDate":          end_dt.strftime("%Y-%m-%d"),
        }
        r = requests.post(
            "https://api.dhan.co/v2/charts/intraday",
            headers=HEADERS, json=payload, timeout=15
        )
        data = r.json()
        # Dhan intraday API returns flat structure: {"open":[...], "close":[...]}
        # Some responses wrap it under "data" key — handle both
        raw = data.get("data", data)
        timestamps = raw.get("timestamp", [])
        if r.status_code == 200 and timestamps:
            print(f"   ✅ {sym:14} {len(timestamps)} candles fetched ✓")
        elif r.status_code == 200 and "open" in raw:
            bars = len(raw.get("open", []))
            print(f"   ✅ {sym:14} {bars} candles fetched ✓")
        elif r.status_code == 200:
            print(f"   ⚠️  {sym:14} 0 candles (weekend/holiday — no trading days in range)")
        else:
            print(f"   ❌ {sym:14} {str(data)[:120]}")
    except Exception as e:
        print(f"   ❌ {sym:14} Error: {e}")

# ── Summary ──
passed = sum(ltp_results.values())
total  = len(ltp_results)
print(f"\n{'='*60}")
print(f"  📊  Results: {passed}/{total} instruments responding")
if passed == total:
    print("  ✅  All instruments OK — ready to run backtest & live trading")
elif passed >= total * 0.6:
    print("  ⚠️  Most instruments OK — check failed ones above")
else:
    print("  ❌  Many failures — token may be expired or IDs wrong")
print(f"{'='*60}\n")
