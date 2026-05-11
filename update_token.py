"""
Dhan Token Updater
─────────────────────────────────────────────
Dhan access tokens expire every 24 hours.
Run this every morning before market opens to refresh your token.

Steps:
  1. Go to https://web.dhan.co  →  Profile  →  API section
  2. Copy your new Access Token
  3. Run: python3 update_token.py
  4. Paste the token when prompted
"""

import os, re, sys, requests
from dotenv import load_dotenv

ENV_FILE = os.path.join(os.path.dirname(__file__), ".env")

print("\n" + "="*55)
print("  🔑  DHAN TOKEN UPDATER")
print("="*55)
print("\nSteps to get your new token:")
print("  1. Open  https://web.dhan.co")
print("  2. Go to  Profile (top-right) → API")
print("  3. Click  'Generate / Copy Access Token'")
print("  4. Paste it below\n")

new_token = input("Paste new Access Token: ").strip()

if not new_token or len(new_token) < 20:
    print("❌ Token looks too short — please try again.")
    sys.exit(1)

# Verify the token works before saving
load_dotenv(ENV_FILE)
client_id = os.getenv("DHAN_CLIENT_ID", "")
if not client_id:
    print("❌ DHAN_CLIENT_ID not found in .env")
    sys.exit(1)

print("\n🔄 Verifying token with Dhan API...")
try:
    r = requests.get(
        "https://api.dhan.co/v2/fundlimit",
        headers={
            "access-token":  new_token,
            "client-id":     client_id,
            "Content-Type":  "application/json",
        },
        timeout=10
    )
    if r.status_code == 200:
        bal = r.json().get("availabelBalance", r.json().get("availableBalance", "N/A"))
        print(f"✅ Token valid!  Available funds: ₹{bal}")
    elif r.status_code == 401:
        print("❌ Token is invalid — please copy it again from dhanhq.co")
        sys.exit(1)
    else:
        print(f"⚠️  Unexpected response ({r.status_code}) — saving anyway")
except Exception as e:
    print(f"⚠️  Could not verify online ({e}) — saving anyway")

# Read current .env
with open(ENV_FILE, "r") as f:
    content = f.read()

# Replace or append token
if "DHAN_ACCESS_TOKEN" in content:
    content = re.sub(
        r"DHAN_ACCESS_TOKEN\s*=.*",
        f"DHAN_ACCESS_TOKEN={new_token}",
        content
    )
else:
    content += f"\nDHAN_ACCESS_TOKEN={new_token}\n"

with open(ENV_FILE, "w") as f:
    f.write(content)

print(f"\n✅ Token saved to .env")
print("▶  You can now run the trading system:\n")
print("   python3 main.py           # paper trading")
print("   python3 main.py --live    # live trading")
print("   python3 test_api.py       # verify all instruments\n")
