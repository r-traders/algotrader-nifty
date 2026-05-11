"""
Telegram Connection Test
Tests your Telegram bot and sends a test alert.

Run: python3 test_telegram.py
"""

import os
import sys
import requests
from datetime import datetime
from dotenv import load_dotenv
load_dotenv()

TOKEN   = os.getenv("TELEGRAM_BOT_TOKEN", "")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

print("\n" + "="*50)
print("  📱  TELEGRAM CONNECTION TEST")
print("="*50)

# Step 1: Check config
print("\n1️⃣  Checking .env config...")
if not TOKEN:
    print("   ❌ TELEGRAM_BOT_TOKEN is missing in .env")
    print("   → Create a bot via @BotFather on Telegram")
    print("   → Paste the token in .env as TELEGRAM_BOT_TOKEN=xxx")
    sys.exit(1)
else:
    print(f"   ✅ Bot Token found: {TOKEN[:10]}...")

if not CHAT_ID:
    print("   ❌ TELEGRAM_CHAT_ID is missing in .env")
    print("   → Message your bot on Telegram")
    print("   → Visit: https://api.telegram.org/bot{TOKEN}/getUpdates")
    print("   → Find 'chat':{'id': XXXXXXX} and paste that number in .env")
    sys.exit(1)
else:
    print(f"   ✅ Chat ID found: {CHAT_ID}")

# Step 2: Verify bot
print("\n2️⃣  Verifying bot token...")
try:
    resp = requests.get(
        f"https://api.telegram.org/bot{TOKEN}/getMe",
        timeout=10
    )
    if resp.status_code == 200:
        bot = resp.json().get("result", {})
        print(f"   ✅ Bot verified!")
        print(f"   🤖 Bot Name    : {bot.get('first_name')}")
        print(f"   🔖 Bot Username: @{bot.get('username')}")
    else:
        print(f"   ❌ Invalid token: {resp.text[:100]}")
        sys.exit(1)
except Exception as e:
    print(f"   ❌ Error: {e}")
    sys.exit(1)

# Step 3: Send test message
print("\n3️⃣  Sending test alert to your Telegram...")
msg = f"""
🤖 <b>AlgoTrader — Test Alert</b>

✅ Your trading bot is connected and working!

📊 <b>System Info:</b>
• Mode: Paper Trading
• Capital: ₹1,20,000
• Broker: Dhan (NSE)
• Strategies: Option Chain + SMC + Indicators

📅 Test sent at: {datetime.now().strftime("%d %b %Y, %H:%M:%S")}

<i>You will receive trade alerts here when the market opens on Monday 9:15 AM IST</i>
"""

try:
    resp = requests.post(
        f"https://api.telegram.org/bot{TOKEN}/sendMessage",
        json={
            "chat_id": CHAT_ID,
            "text": msg,
            "parse_mode": "HTML",
        },
        timeout=10
    )
    if resp.status_code == 200:
        print("   ✅ Test message sent successfully!")
        print("   📱 Check your Telegram — you should see the alert now")
    elif resp.status_code == 400:
        data = resp.json()
        print(f"   ❌ Bad request: {data.get('description')}")
        if "chat not found" in str(data).lower():
            print("   → Make sure you have sent at least one message to your bot first")
            print("   → Open Telegram → search your bot → click Start")
    else:
        print(f"   ❌ Failed: {resp.text[:150]}")
except Exception as e:
    print(f"   ❌ Error: {e}")

# Step 4: How to get Chat ID if missing
print("\n4️⃣  How to find your Chat ID (if needed):")
print(f"   Open this URL in your browser:")
print(f"   https://api.telegram.org/bot{TOKEN[:10]}[...]/getUpdates")
print(f"   Look for: \"chat\":{{\"id\": 123456789}}")
print(f"   That number is your TELEGRAM_CHAT_ID")

print("\n" + "="*50)
print("  ✅  Telegram test complete!")
print("="*50 + "\n")
