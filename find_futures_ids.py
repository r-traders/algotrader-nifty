"""
find_futures_ids.py  —  Run this locally to find correct April 2026 futures security IDs.

Usage:
    cd trading_system
    python3 find_futures_ids.py

This script tries three methods (in order) to locate NIFTY & BANKNIFTY Apr-2026 FUTIDX IDs:
  1. Download Dhan's public instrument master CSV
  2. Dhan search API  (/instruments/search)
  3. Dhan full NSE_FNO instrument list
"""

import sys, os, io, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

import requests as _req
from config.settings import DHAN_CLIENT_ID, DHAN_ACCESS_TOKEN

TARGET_SYMBOLS  = ["NIFTY", "BANKNIFTY"]
TARGET_MONTH    = "2026-04"          # ISO prefix — April 2026
DHAN_BASE       = "https://api.dhan.co/v2"
CSV_URL         = "https://images.dhan.co/api-data/api-scrip-master.csv"

AUTH_HEADERS = {
    "access-token":  DHAN_ACCESS_TOKEN,
    "client-id":     DHAN_CLIENT_ID,
    "Content-Type":  "application/json",
    "Accept":        "application/json",
}


# ─────────────────────────────────────────────────────────────────────────────
# METHOD 1  — Public instrument master CSV
# ─────────────────────────────────────────────────────────────────────────────

def try_csv_master() -> dict:
    """Download Dhan's public instrument master and filter for Apr-2026 FUTIDX."""
    print("\n[Method 1] Downloading instrument master CSV …")
    try:
        r = _req.get(CSV_URL, timeout=30)
        r.raise_for_status()
    except Exception as e:
        print(f"  ✗ CSV download failed: {e}")
        return {}

    import csv
    reader = csv.DictReader(io.StringIO(r.text))
    results = {}
    for row in reader:
        # Column names vary slightly — normalise them
        instr  = (row.get("SEM_INSTRUMENT_NAME") or row.get("INSTRUMENT_TYPE") or "").upper()
        exch   = (row.get("SEM_EXM_EXCH_ID")     or row.get("EXCHANGE_SEGMENT") or "").upper()
        expiry = (row.get("SEM_EXPIRY_DATE")      or row.get("EXPIRY_DATE")      or "")
        sym    = (row.get("SEM_TRADING_SYMBOL")   or row.get("TRADING_SYMBOL")   or "").upper()
        sid    = (row.get("SEM_SMST_SECURITY_ID") or row.get("SECURITY_ID")      or "")

        if instr != "FUTIDX":
            continue
        if "NSE" not in exch and "NSE_FNO" not in exch:
            continue
        if not expiry.startswith(TARGET_MONTH):
            continue

        for tsym in TARGET_SYMBOLS:
            if sym.startswith(tsym):
                results.setdefault(tsym, []).append({
                    "security_id":    sid,
                    "trading_symbol": sym,
                    "expiry":         expiry,
                    "exchange":       exch,
                    "instrument":     instr,
                })

    return results


# ─────────────────────────────────────────────────────────────────────────────
# METHOD 2  — Dhan search API
# ─────────────────────────────────────────────────────────────────────────────

def try_search_api() -> dict:
    """Use Dhan /instruments/search to find futures."""
    print("\n[Method 2] Dhan /instruments/search …")
    results = {}

    for sym in TARGET_SYMBOLS:
        time.sleep(0.3)
        try:
            r = _req.get(
                f"{DHAN_BASE}/instruments/search",
                params={"search": sym},
                headers=AUTH_HEADERS,
                timeout=10,
            )
            items = r.json() if r.status_code == 200 else []
            if isinstance(items, dict):
                items = items.get("data", items.get("instruments", []))
            if not isinstance(items, list):
                print(f"  {sym}: unexpected response type {type(items)} — {str(items)[:200]}")
                continue

            for i in items:
                instr  = str(i.get("instrumentType",   i.get("instrument_type",   ""))).upper()
                exch   = str(i.get("exchangeSegment",  i.get("exchange_segment",  ""))).upper()
                expiry = str(i.get("expiryDate",       i.get("expiry_date",       i.get("expiry", ""))))
                tsym   = str(i.get("tradingSymbol",    i.get("trading_symbol",    ""))).upper()
                sid    = str(i.get("securityId",       i.get("security_id",       i.get("SEM_SMST_SECURITY_ID", ""))))

                if instr != "FUTIDX":
                    continue
                if not expiry.startswith(TARGET_MONTH):
                    continue

                results.setdefault(sym, []).append({
                    "security_id":    sid,
                    "trading_symbol": tsym,
                    "expiry":         expiry,
                    "exchange":       exch,
                    "raw_keys":       list(i.keys()),
                })

        except Exception as e:
            print(f"  {sym}: error — {e}")

    return results


# ─────────────────────────────────────────────────────────────────────────────
# METHOD 3  — Dhan NSE_FNO segment instrument list
# ─────────────────────────────────────────────────────────────────────────────

def try_segment_list() -> dict:
    """Download Dhan's NSE_FNO instrument list (if available)."""
    print("\n[Method 3] Dhan /instruments/NSE_FNO segment list …")
    results = {}
    # Dhan sometimes exposes a segment-specific instrument CSV
    seg_url = "https://images.dhan.co/api-data/api-scrip-master-detailed.csv"
    try:
        r = _req.get(seg_url, timeout=30)
        r.raise_for_status()
    except Exception as e:
        print(f"  ✗ Segment list download failed: {e}")
        return {}

    import csv
    reader = csv.DictReader(io.StringIO(r.text))
    for row in reader:
        instr  = (row.get("SEM_INSTRUMENT_NAME") or "").upper()
        exch   = (row.get("SEM_EXM_EXCH_ID")     or "").upper()
        expiry = (row.get("SEM_EXPIRY_DATE")      or "")
        sym    = (row.get("SEM_TRADING_SYMBOL")   or "").upper()
        sid    = (row.get("SEM_SMST_SECURITY_ID") or "")

        if instr != "FUTIDX":
            continue
        if "NSE" not in exch:
            continue
        if not expiry.startswith(TARGET_MONTH):
            continue

        for tsym in TARGET_SYMBOLS:
            if sym.startswith(tsym):
                results.setdefault(tsym, []).append({
                    "security_id":    sid,
                    "trading_symbol": sym,
                    "expiry":         expiry,
                })
    return results


# ─────────────────────────────────────────────────────────────────────────────
# QUICK LTP TEST
# ─────────────────────────────────────────────────────────────────────────────

def test_ltp(security_id: str, sym: str) -> str:
    try:
        payload = {"NSE_FNO": [int(security_id)], "dhanClientId": DHAN_CLIENT_ID}
        r = _req.post(
            f"{DHAN_BASE}/marketfeed/ltp",
            json=payload,
            headers=AUTH_HEADERS,
            timeout=10,
        )
        data = r.json()
        # Look for last_price anywhere in response
        raw_str = str(data)
        if "last_price" in raw_str:
            import re
            m = re.search(r"'last_price':\s*([0-9.]+)", raw_str)
            return f"✅  LTP = {m.group(1)}" if m else "✅  last_price found"
        if r.status_code == 429:
            return "⚠️  Rate limited (429)"
        return f"❌  No last_price — HTTP {r.status_code} — {raw_str[:120]}"
    except Exception as e:
        return f"❌  Error: {e}"


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 60)
    print("  Dhan Futures ID Finder — April 2026")
    print("=" * 60)

    found = {}

    # Try all three methods, stop when we have results
    for method_fn in [try_csv_master, try_search_api, try_segment_list]:
        res = method_fn()
        if res:
            found = res
            break
        print("  (no results — trying next method)")

    if not found:
        print("\n❌  Could not find April 2026 futures from any source.")
        print("   → Open http://localhost:8080/api/debug/find_futures to check via the dashboard.")
        sys.exit(1)

    print("\n" + "=" * 60)
    print("  RESULTS")
    print("=" * 60)

    for sym in TARGET_SYMBOLS:
        contracts = found.get(sym, [])
        if not contracts:
            print(f"\n{sym}: ❌  No April 2026 FUTIDX found")
            continue

        print(f"\n{sym}  — {len(contracts)} contract(s) found:")
        for c in contracts:
            sid = c['security_id']
            print(f"   security_id = {sid}  |  symbol = {c.get('trading_symbol', '-')}  |  expiry = {c.get('expiry', '-')}")

        # Test LTP for the first (nearest) contract
        best = contracts[0]
        sid  = best["security_id"]
        print(f"   LTP test for {sid} … ", end="", flush=True)
        time.sleep(0.5)
        ltp_result = test_ltp(sid, sym)
        print(ltp_result)

    print("\n" + "=" * 60)
    print("  UPDATE INSTRUMENT_IDS IN app.py")
    print("=" * 60)
    print("""
Paste the correct values into INSTRUMENT_IDS in:
  trading_system/dashboard/app.py

Example:
    INSTRUMENT_IDS = {
        "NIFTY": {
            "security_id": "<ID from above>",
            "exchange":    "NSE_FNO",
            "instrument":  "FUTIDX",
            "label":       "NIFTY Apr FUT",
            "expiry":      "<expiry from above>",
        },
        "BANKNIFTY": {
            "security_id": "<ID from above>",
            "exchange":    "NSE_FNO",
            "instrument":  "FUTIDX",
            "label":       "BANKNIFTY Apr FUT",
            "expiry":      "<expiry from above>",
        },
    }
""")
