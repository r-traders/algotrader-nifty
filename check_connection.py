"""
Quick connection test - checks Dhan API credentials and connectivity.
Run: python3 check_connection.py
"""

import sys
import os
from dotenv import load_dotenv
load_dotenv()

print("\n" + "="*50)
print("  🔌  DHAN API CONNECTION TEST")
print("="*50)

# Step 1: Check credentials loaded
client_id = os.getenv("DHAN_CLIENT_ID", "")
access_token = os.getenv("DHAN_ACCESS_TOKEN", "")

print(f"\n1️⃣  Credentials in .env:")
print(f"   Client ID    : {'✅ Found (' + client_id[:6] + '***)' if client_id else '❌ MISSING'}")
print(f"   Access Token : {'✅ Found (' + access_token[:10] + '...)' if access_token else '❌ MISSING'}")

if not client_id or not access_token:
    print("\n❌ Credentials missing! Check your .env file.")
    sys.exit(1)

# Step 2: Test internet connectivity
print("\n2️⃣  Testing internet connection...")
import urllib.request
try:
    urllib.request.urlopen("https://api.dhan.co", timeout=5)
    print("   ✅ Internet OK — Dhan API reachable")
except Exception as e:
    print(f"   ❌ Cannot reach Dhan API: {e}")
    sys.exit(1)

# Step 3: Test Dhan API auth
print("\n3️⃣  Testing Dhan API authentication...")
import requests
try:
    resp = requests.get(
        "https://api.dhan.co/v2/fundlimit",
        headers={
            "access-token": access_token,
            "client-id": client_id,
            "Content-Type": "application/json",
        },
        timeout=10
    )
    print(f"   HTTP Status: {resp.status_code}")
    if resp.status_code == 200:
        data = resp.json()
        print(f"   ✅ Authentication SUCCESS!")
        print(f"   💰 Available Funds: ₹{data.get('availabelBalance', data.get('availableBalance', 'N/A'))}")
    elif resp.status_code == 401:
        print("   ❌ Authentication FAILED — Invalid credentials")
        print("   → Regenerate your Access Token on dhanhq.co")
    else:
        print(f"   ⚠️  Unexpected response: {resp.text[:200]}")
except requests.exceptions.Timeout:
    print("   ❌ Request timed out — Dhan API not responding")
except Exception as e:
    print(f"   ❌ Error: {e}")

# Step 4: Test market data — try NSE_IDX (index) and NSE_EQ (equity)
HEADERS = {
    "access-token": access_token,
    "client-id": client_id,
    "Content-Type": "application/json",
}

print("\n4️⃣  Testing market data...")

import time

tests = [
    ("NIFTY 50 Index  (NSE_IDX / 13)",   {"NSE_IDX": ["13"]}),
    ("BANKNIFTY Index (NSE_IDX / 25)",   {"NSE_IDX": ["25"]}),
    ("RELIANCE Equity (NSE_EQ  / 2885)", {"NSE_EQ":  ["2885"]}),
    ("HDFCBANK Equity (NSE_EQ  / 1333)", {"NSE_EQ":  ["1333"]}),
]

working_ids = []
for label, payload in tests:
    try:
        resp = requests.post(
            "https://api.dhan.co/v2/marketfeed/ltp",
            headers=HEADERS, json=payload, timeout=10
        )
        data = resp.json()
        status = data.get("status", "")
        if resp.status_code == 200 and status != "failed":
            ltp = list(data.get("data", {}).values())
            ltp_val = ltp[0].get("last_price", ltp[0]) if ltp and isinstance(ltp[0], dict) else ltp
            note = "(markets closed — LTP empty is normal on weekends)" if not ltp_val or ltp_val == [] else ""
            print(f"   ✅ {label}  →  LTP: {ltp_val} {note}")
            working_ids.append((label, payload))
        else:
            err = str(data)[:100]
            print(f"   ❌ {label}  →  {err}")
    except Exception as e:
        print(f"   ❌ {label}  →  Error: {e}")
    time.sleep(1)   # 1-second delay to avoid rate limiting (805 error)

# Step 5: Test historical candle data (needed for backtest)
print("\n5️⃣  Testing historical candle data (NIFTY INDEX 15-min, last 10 days)...")
import datetime
time.sleep(1)
for sec_id, exch, instr, name in [
    ("13",   "NSE_IDX", "INDEX",  "NIFTY INDEX"),
    ("2885", "NSE_EQ",  "EQUITY", "RELIANCE EQ"),
]:
    try:
        end   = datetime.date.today().strftime("%Y-%m-%d")
        start = (datetime.date.today() - datetime.timedelta(days=10)).strftime("%Y-%m-%d")
        resp = requests.post(
            "https://api.dhan.co/v2/charts/intraday",
            headers=HEADERS,
            json={
                "securityId":      sec_id,
                "exchangeSegment": exch,
                "instrument":      instr,
                "interval":        "15",
                "oi":              False,
                "fromDate":        start,
                "toDate":          end,
            },
            timeout=15
        )
        data = resp.json()
        if resp.status_code == 200 and "data" in data:
            bars = len(data["data"].get("timestamp", []))
            print(f"   ✅ {name}  →  {bars} candles fetched (last 10 days)")
        else:
            print(f"   ❌ {name}  →  {str(data)[:120]}")
    except Exception as e:
        print(f"   ❌ {name}  →  Error: {e}")
    time.sleep(1)

print("\n" + "="*50)
print("  Connection test complete!")
print("="*50 + "\n")
