"""
Dhan Security ID Finder
─────────────────────────────────────────────
Reads the Dhan instruments CSV and finds the correct
security IDs for all symbols used in main.py.

Run: python3 find_security_ids.py
"""

import os, sys, csv, glob

# Symbols we need
SYMBOLS = {
    "NSE_EQ":  ["RELIANCE", "TCS", "HDFCBANK", "INFY", "ICICIBANK", "SBIN"],
    "NSE_IDX": ["NIFTY 50", "NIFTY BANK", "NIFTY MIDCAP SELECT"],
    "NSE_FNO": ["NIFTY", "BANKNIFTY", "MIDCPNIFTY"],
}

# Try to find the CSV in common locations
SEARCH_PATHS = [
    os.path.expanduser("~/Downloads/api-scrip-master.csv"),
    os.path.expanduser("~/Desktop/api-scrip-master.csv"),
    os.path.expanduser("~/Documents/api-scrip-master.csv"),
]
# Also search Downloads folder for any matching file
SEARCH_PATHS += glob.glob(os.path.expanduser("~/Downloads/*scrip*.csv"))
SEARCH_PATHS += glob.glob(os.path.expanduser("~/Downloads/*master*.csv"))
SEARCH_PATHS += glob.glob(os.path.expanduser("~/Downloads/*dhan*.csv"))

print("\n" + "="*60)
print("  🔍  DHAN SECURITY ID FINDER")
print("="*60)

# Find the CSV
csv_path = None
for p in SEARCH_PATHS:
    if os.path.exists(p):
        csv_path = p
        break

if not csv_path:
    print("\n❌ Could not find api-scrip-master.csv")
    print("\nPlease download it from:")
    print("   https://images.dhan.co/api-data/api-scrip-master.csv")
    print("\nThen run this script again.")

    manual = input("\nOr enter the full path to the CSV file: ").strip()
    if manual and os.path.exists(manual):
        csv_path = manual
    else:
        sys.exit(1)

print(f"\n✅ Found CSV: {csv_path}")

# Read and search
print("\nSearching for security IDs...\n")

found = {}
eq_symbols  = [s.upper() for s in SYMBOLS["NSE_EQ"]]
fno_symbols = [s.upper() for s in SYMBOLS["NSE_FNO"]]
idx_symbols = [s.upper() for s in SYMBOLS["NSE_IDX"]]

try:
    with open(csv_path, newline="", encoding="utf-8", errors="ignore") as f:
        reader = csv.DictReader(f)
        headers = reader.fieldnames
        print(f"  CSV columns: {', '.join(headers[:8]) if headers else 'unknown'}\n")

        for row in reader:
            # Dhan column names
            sym      = (row.get("SEM_TRADING_SYMBOL") or row.get("TRADING_SYMBOL") or "").upper().strip()
            sec_id   = (row.get("SEM_SMST_SECURITY_ID") or row.get("SECURITY_ID") or "").strip()
            exch     = (row.get("SEM_EXM_EXCH_ID") or row.get("EXCHANGE") or "").upper().strip()
            instr    = (row.get("SEM_INSTRUMENT_NAME") or row.get("INSTRUMENT") or "").upper().strip()
            custom   = (row.get("SM_SYMBOL_NAME") or "").upper().strip()

            # NSE Equities
            if exch == "NSE" and instr == "EQUITY" and sym in eq_symbols:
                key = f"NSE_EQ:{sym}"
                if key not in found:
                    found[key] = sec_id
                    print(f"  ✅ {sym:14} NSE_EQ   ID: {sec_id}")

            # NSE FNO — Futures (FUTIDX)
            if exch == "NSE" and instr == "FUTIDX":
                for fsym in fno_symbols:
                    if sym == fsym or sym.startswith(fsym):
                        key = f"NSE_FNO:{fsym}"
                        if key not in found:
                            found[key] = sec_id
                            print(f"  ✅ {fsym:14} NSE_FNO  ID: {sec_id}  (symbol: {sym})")
                            break

            # NSE Index
            if exch == "NSE" and instr in ("INDEX", ""):
                for isym in idx_symbols:
                    if isym in sym or sym in isym:
                        key = f"NSE_IDX:{isym}"
                        if key not in found:
                            found[key] = sec_id
                            print(f"  ✅ {isym:20} NSE_IDX  ID: {sec_id}")
                            break

except Exception as e:
    print(f"❌ Error reading CSV: {e}")
    sys.exit(1)

# Print update instructions
print("\n" + "="*60)
print("  📋  Copy these into main.py INSTRUMENT_REGISTRY")
print("="*60)
print()

# Map results to our symbols
mapping = {
    "NIFTY":       found.get("NSE_IDX:NIFTY 50")    or found.get("NSE_FNO:NIFTY"),
    "BANKNIFTY":   found.get("NSE_IDX:NIFTY BANK")  or found.get("NSE_FNO:BANKNIFTY"),
    "MIDCAPNIFTY": found.get("NSE_IDX:NIFTY MIDCAP SELECT") or found.get("NSE_FNO:MIDCPNIFTY"),
    "RELIANCE":    found.get("NSE_EQ:RELIANCE"),
    "TCS":         found.get("NSE_EQ:TCS"),
    "HDFCBANK":    found.get("NSE_EQ:HDFCBANK"),
    "INFY":        found.get("NSE_EQ:INFY"),
    "ICICIBANK":   found.get("NSE_EQ:ICICIBANK"),
    "SBIN":        found.get("NSE_EQ:SBIN"),
}

for sym, sid in mapping.items():
    if sid:
        print(f'    "{sym}": {{"security_id": "{sid}", ...}}')
    else:
        print(f'    "{sym}": ❌ NOT FOUND — search manually in the CSV')

print()

# Also write a patch file
patch_file = os.path.join(os.path.dirname(__file__), "security_ids_found.txt")
with open(patch_file, "w") as f:
    f.write("Dhan Security IDs — found from api-scrip-master.csv\n\n")
    for sym, sid in mapping.items():
        f.write(f"{sym}: {sid or 'NOT FOUND'}\n")
print(f"✅ Results saved to: security_ids_found.txt")
print()
