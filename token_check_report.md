# Dhan Token Check — 2026-05-04 08:30 IST (scheduled task)

## Result: TOKEN EXPIRED ⚠️

Determined by decoding the JWT in `.env` (`DHAN_ACCESS_TOKEN`). A direct call
to `GET https://api.dhan.co/v2/fundlimit` could not be made from the
scheduled-task sandbox — see "Telegram alert" below for the network
restriction. JWT claims:

- **Issued at (`iat`)**: 2026-04-10 08:55:43 UTC
- **Expires at (`exp`)**: 2026-04-11 08:55:43 UTC
- **Now**: 2026-05-04 03:14 UTC
- **Expired by**: ~22.7 days (~546 hours)

The Dhan access token is well past its 24-hour expiry window. The trading
system will NOT be able to authenticate against `api.dhan.co` until the token
is refreshed.

## Telegram alert: NOT SENT (network blocked)

The scheduled-task sandbox has an egress allowlist that does not include
`api.telegram.org` or `api.dhan.co`. The workspace `web_fetch` tool returned
`cowork-egress-blocked` for both hosts on this run, consistent with the
2026-04-25, 2026-04-27, and 2026-04-28 runs.

To enable the morning Telegram alert from scheduled tasks, allow these hosts
in **Settings → Desktop app → Capabilities** (or ask the workspace admin on
Team/Enterprise plans):

- `api.telegram.org`
- `api.dhan.co`

Until the allowlist is updated, the Telegram step of this scheduled task will
continue to fail silently, and this report file is the visible signal.

## Action required before 9:15 AM IST

1. Open https://web.dhan.co
2. Go to **Profile → API**
3. Copy the new Access Token
4. From `trading_system/`, run: `python3 update_token.py`

System will not trade until the token is refreshed. Note: the token has been
unrefreshed for over three weeks, so the system has been idle for that period.

## Intended Telegram payload (for reference, not sent)

```
⚠️ AlgoTrader — TOKEN EXPIRED

🔑 Dhan access token has expired!

Action required before 9:15 AM:
1. Open https://web.dhan.co
2. Go to Profile → API
3. Copy new Access Token
4. Run: python3 update_token.py

System will NOT trade until token is refreshed.
```
