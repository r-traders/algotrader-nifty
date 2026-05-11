"""
NIFTY Candle Format Finder
Tests every possible API format to find what works for NIFTY historical data.
Run: python3 test_nifty_candles.py
"""
import os, sys, time, requests
from dotenv import load_dotenv
load_dotenv()

TOKEN = os.getenv("DHAN_ACCESS_TOKEN")
CID   = os.getenv("DHAN_CLIENT_ID")
HEADERS = {"access-token": TOKEN, "client-id": CID, "Content-Type": "application/json"}

START, END = "2026-03-21", "2026-03-28"

print("\n=== NIFTY Candle API Format Test ===\n")

variants = [
    ("NSE_IDX / INDEX  / str id / int interval",
     {"securityId":"13", "exchangeSegment":"NSE_IDX","instrument":"INDEX",   "interval":15, "expiryCode":0, "oi":False,"fromDate":START,"toDate":END}),
    ("NSE_IDX / INDEX  / int id / int interval",
     {"securityId": 13,  "exchangeSegment":"NSE_IDX","instrument":"INDEX",   "interval":15, "expiryCode":0, "oi":False,"fromDate":START,"toDate":END}),
    ("NSE_IDX / INDEX  / no expiryCode",
     {"securityId":"13", "exchangeSegment":"NSE_IDX","instrument":"INDEX",   "interval":15,                 "oi":False,"fromDate":START,"toDate":END}),
    ("NSE_IDX / INDEX  / str interval",
     {"securityId":"13", "exchangeSegment":"NSE_IDX","instrument":"INDEX",   "interval":"15","expiryCode":0,"oi":False,"fromDate":START,"toDate":END}),
    ("NSE_IDX / INDICES / str id",
     {"securityId":"13", "exchangeSegment":"NSE_IDX","instrument":"INDICES", "interval":15, "expiryCode":0, "oi":False,"fromDate":START,"toDate":END}),
    ("NSE_FNO / FUTIDX  / 62329 (Jun2026 fut)",
     {"securityId":"62329","exchangeSegment":"NSE_FNO","instrument":"FUTIDX","interval":15, "expiryCode":0, "oi":False,"fromDate":START,"toDate":END}),
]

winner = None
for label, payload in variants:
    try:
        r = requests.post("https://api.dhan.co/v2/charts/intraday",
                          headers=HEADERS, json=payload, timeout=15)
        d = r.json()
        raw  = d.get("data", d)
        bars = len(raw.get("timestamp", raw.get("open", [])))
        if r.status_code == 200 and bars > 0:
            print(f"  ✅ WORKS: {label}  →  {bars} candles")
            winner = (label, payload)
        elif r.status_code == 200 and "open" in d:
            bars2 = len(d.get("open", []))
            print(f"  ✅ WORKS: {label}  →  {bars2} candles (flat format)")
            winner = (label, payload)
        else:
            err = d.get("errorMessage", d.get("errorCode", str(d)))[:90]
            print(f"  ❌ {label}")
            print(f"     {err}")
    except Exception as e:
        print(f"  ❌ {label}  →  Error: {e}")
    time.sleep(1)

print()
if winner:
    print(f"✅ Use this format for NIFTY:")
    print(f"   {winner[1]}")
else:
    print("❌ No format worked — check API subscription or try tomorrow (market hours)")
print()
