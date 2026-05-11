"""
Dhan API Client
Handles all communication with Dhan broker API
Docs: https://dhanhq.co/docs/v2/
"""

import time
import logging
import requests
from datetime import datetime, date, timedelta
from typing import Optional, Dict, List, Any
from config.settings import DHAN_CLIENT_ID, DHAN_ACCESS_TOKEN, PAPER_TRADING

logger = logging.getLogger(__name__)

DHAN_BASE_URL = "https://api.dhan.co/v2"


class DhanClient:
    """
    Wrapper around Dhan REST API.
    Covers: market data, option chain, order placement, portfolio, positions.
    """

    def __init__(self, client_id: str = DHAN_CLIENT_ID, access_token: str = DHAN_ACCESS_TOKEN):
        self.client_id = client_id
        self.access_token = access_token
        self.session = requests.Session()
        self.session.headers.update({
            "access-token": self.access_token,
            "client-id": self.client_id,
            "Content-Type": "application/json",
            "Accept": "application/json",
        })
        self.paper_trading = PAPER_TRADING
        if self.paper_trading:
            logger.info("⚡ DhanClient initialized in PAPER TRADING mode")
        else:
            logger.warning("🔴 DhanClient initialized in LIVE TRADING mode")

    # ─────────────────────────────────────────────
    # HELPER
    # ─────────────────────────────────────────────
    def _get(self, endpoint: str, params: dict = None) -> Dict:
        url = f"{DHAN_BASE_URL}{endpoint}"
        try:
            resp = self.session.get(url, params=params, timeout=10)
            resp.raise_for_status()
            return resp.json()
        except requests.exceptions.RequestException as e:
            logger.error(f"GET {endpoint} failed: {e}")
            return {}

    def _post(self, endpoint: str, payload: dict) -> Dict:
        url = f"{DHAN_BASE_URL}{endpoint}"
        try:
            # Dhan v2 API requires dhanClientId in the body for most POST endpoints
            body_with_id = dict(payload)
            if "dhanClientId" not in body_with_id:
                body_with_id["dhanClientId"] = self.client_id

            resp = self.session.post(url, json=body_with_id, timeout=10)
            # Always parse JSON — return error body so callers can inspect errors
            try:
                body = resp.json()
            except Exception:
                body = {"_raw": resp.text[:300]}
            if resp.status_code not in (200, 201):
                logger.error(f"POST {endpoint} HTTP {resp.status_code}: {resp.text[:300]}")
                body["_http_status"] = resp.status_code
            return body
        except requests.exceptions.RequestException as e:
            logger.error(f"POST {endpoint} connection error: {e}")
            return {"_error": str(e)}

    def _delete(self, endpoint: str) -> Dict:
        url = f"{DHAN_BASE_URL}{endpoint}"
        try:
            resp = self.session.delete(url, timeout=10)
            resp.raise_for_status()
            return resp.json()
        except requests.exceptions.RequestException as e:
            logger.error(f"DELETE {endpoint} failed: {e}")
            return {}

    # ─────────────────────────────────────────────
    # MARKET DATA
    # ─────────────────────────────────────────────
    def get_ltp(self, exchange: str, symbol: str, security_id: str) -> Optional[float]:
        """
        Get Last Traded Price for a symbol.
        Dhan v2 response: {"data": {"IDX_I": {"13": {"last_price": 22677.2}}}, "status": "success"}
        """
        sid_int = int(security_id)
        payload = {exchange: [sid_int]}
        data = self._post("/marketfeed/ltp", payload)
        if not data:
            return None
        # Abort early if the response signals an error (rate-limit, bad request, etc.)
        if data.get("_http_status") or data.get("status") == "failed":
            logger.debug(f"[LTP] API error for {symbol}: {str(data)[:200]}")
            return None

        try:
            d = data.get("data", {})
            if not d or not isinstance(d, dict):
                return None

            # Walk every level of nesting to find {"last_price": <numeric>}
            # Stops as soon as a valid numeric last_price is found.
            def _find_lp(obj):
                if isinstance(obj, dict):
                    if "last_price" in obj:
                        v = obj["last_price"]
                        try:
                            fv = float(v)
                            if fv > 0:
                                return fv
                        except (TypeError, ValueError):
                            pass   # value is not numeric; keep walking
                    for val in obj.values():
                        if isinstance(val, dict):   # only recurse into dicts
                            found = _find_lp(val)
                            if found:
                                return found
                return None

            price = _find_lp(d)
            if price:
                return price

            logger.debug(f"[LTP] No last_price found in response for {symbol}. data={str(d)[:200]}")
            return None
        except (KeyError, TypeError, ValueError) as e:
            logger.debug(f"[LTP] Parse error for {symbol}: {e}")
            return None

    def get_quote(self, exchange: str, security_id: str) -> Dict:
        """Get full quote (LTP, OHLC, volume, OI for F&O)."""
        payload = {exchange: [security_id]}
        return self._post("/marketfeed/quote", payload)

    def get_ohlc(self, exchange: str, security_id: str) -> Dict:
        """Get OHLC data."""
        payload = {exchange: [security_id]}
        return self._post("/marketfeed/ohlc", payload)

    def get_historical_data(
        self,
        security_id: str,
        exchange_segment: str,
        instrument_type: str,
        expiry_code: int = 0,
        from_date: str = None,
        to_date: str = None,
        chart_type: str = "candle",
        interval: str = "5"
    ) -> List[Dict]:
        """
        Fetch intraday OHLCV candle data.
        interval: 1, 5, 15, 25, 60 (minutes)
        Dhan API endpoint: POST /charts/intraday
        """
        if from_date is None:
            from_date = date.today().strftime("%Y-%m-%d")
        if to_date is None:
            to_date = date.today().strftime("%Y-%m-%d")

        payload = {
            "securityId": security_id,
            "exchangeSegment": exchange_segment,
            "instrument": instrument_type,
            "interval": interval,
            "oi": True,
            "fromDate": from_date,
            "toDate": to_date,
        }
        # Use /charts/intraday for minute-level data
        data = self._post("/charts/intraday", payload)
        if not data or "data" not in data:
            return []
        return self._parse_candles(data["data"])

    def get_daily_candles(
        self,
        security_id: str,
        exchange_segment: str,
        instrument_type: str,
        expiry_code: int = 0,
        from_date: str = None,
        to_date: str = None,
    ) -> List[Dict]:
        """
        Fetch daily (EOD) OHLCV candle data.
        Dhan API endpoint: POST /charts/historical
        Use this for CPR calculation — needs previous day H/L/C.
        """
        if from_date is None:
            from_date = (date.today() - timedelta(days=30)).strftime("%Y-%m-%d")
        if to_date is None:
            to_date = date.today().strftime("%Y-%m-%d")

        payload = {
            "securityId":      security_id,
            "exchangeSegment": exchange_segment,
            "instrument":      instrument_type,
            "expiryCode":      expiry_code,
            "oi":              True,
            "fromDate":        from_date,
            "toDate":          to_date,
        }
        data = self._post("/charts/historical", payload)
        if not data:
            logger.warning(f"[DhanClient] get_daily_candles: empty response for {security_id}")
            return []
        # /charts/historical returns OHLCV arrays directly (no "data" wrapper)
        # /charts/intraday wraps in {"data": {...}} — handle both
        raw = data.get("data", data)
        return self._parse_candles(raw)

    def _parse_candles(self, raw: Dict) -> List[Dict]:
        """Convert Dhan raw candle data to OHLCV list."""
        candles = []
        timestamps = raw.get("timestamp", [])
        opens = raw.get("open", [])
        highs = raw.get("high", [])
        lows = raw.get("low", [])
        closes = raw.get("close", [])
        volumes = raw.get("volume", [])
        oi = raw.get("oi", [None] * len(timestamps))

        for i, ts in enumerate(timestamps):
            candles.append({
                "timestamp": datetime.fromtimestamp(ts),
                "open": opens[i],
                "high": highs[i],
                "low": lows[i],
                "close": closes[i],
                "volume": volumes[i],
                "oi": oi[i] if oi else None,
            })
        return candles

    # ─────────────────────────────────────────────
    # OPTION CHAIN
    # ─────────────────────────────────────────────

    # Dhan v2 requires UnderlyingScrip as INTEGER security ID (not symbol string)
    # These are the IDX_I segment IDs — stable, never change
    _INDEX_SCRIP_IDS: dict = {
        "NIFTY":      13,
        "BANKNIFTY":  25,
        "FINNIFTY":   27,
        "MIDCPNIFTY": 442,
        "SENSEX":     1,
        "BANKEX":     12,
    }

    def _scrip_id(self, underlying: str) -> int:
        """
        Convert symbol name OR numeric string to integer security ID.
        Dhan error 814 'Invalid Request' is almost always caused by passing a string symbol.
        """
        if isinstance(underlying, int):
            return underlying
        up = str(underlying).upper().strip()
        if up in self._INDEX_SCRIP_IDS:
            return self._INDEX_SCRIP_IDS[up]
        try:
            return int(up)
        except (ValueError, TypeError):
            logger.warning(f"[OC] Unknown underlying '{underlying}' — passing as-is")
            return underlying   # type: ignore

    def get_option_chain(self, underlying: str, expiry_date: str) -> Dict:
        """
        Fetch option chain for an underlying (e.g., NIFTY, BANKNIFTY).
        expiry_date: "YYYY-MM-DD"

        Dhan v2 correct field names (confirmed from official docs):
          UnderlyingScrip  = integer security ID (13=NIFTY, 25=BANKNIFTY)
          UnderlyingSeg    = "IDX_I"      ← NOT "UnderlyingSegment"
          Expiry           = "YYYY-MM-DD" ← NOT "UnderlyingExpiry"
        Rate limit: 1 request per 3 seconds.
        """
        sid = self._scrip_id(underlying)
        time.sleep(0.2)   # respect rate limit — callers should also add their own delay
        resp = self._post("/optionchain", {
            "UnderlyingScrip": sid,
            "UnderlyingSeg":   "IDX_I",
            "Expiry":          expiry_date,
        })
        if resp.get("_http_status") or resp.get("_error"):
            # Retry without segment (fallback for older endpoints)
            time.sleep(0.3)
            resp = self._post("/optionchain", {
                "UnderlyingScrip": sid,
                "Expiry":          expiry_date,
            })
        return resp

    def get_option_chain_all_expiries(self, underlying: str) -> Dict:
        """
        Fetch option chain expiry list for an underlying.

        Dhan v2 correct field names (confirmed from official docs):
          UnderlyingScrip = integer security ID (13=NIFTY, 25=BANKNIFTY)
          UnderlyingSeg   = "IDX_I"  ← NOT "UnderlyingSegment"
        Rate limit: 1 request per 3 seconds.
        """
        sid = self._scrip_id(underlying)
        time.sleep(0.2)
        resp = self._post("/optionchain/expirylist", {
            "UnderlyingScrip": sid,
            "UnderlyingSeg":   "IDX_I",
        })
        if not resp.get("_http_status") and not resp.get("_error"):
            return resp
        # Fallback: without segment (for accounts where segment causes issues)
        time.sleep(0.3)
        resp2 = self._post("/optionchain/expirylist", {"UnderlyingScrip": sid})
        if not resp2.get("_http_status") and not resp2.get("_error"):
            return resp2
        return resp2

    # ─────────────────────────────────────────────
    # ORDERS
    # ─────────────────────────────────────────────
    def place_order(
        self,
        security_id: str,
        exchange_segment: str,
        transaction_type: str,       # BUY / SELL
        quantity: int,
        order_type: str,             # LIMIT / MARKET / STOP_LOSS / STOP_LOSS_MARKET
        product_type: str,           # INTRADAY / CNC / MARGIN / MTF / CO / BO
        price: float = 0.0,
        trigger_price: float = 0.0,
        disclosed_quantity: int = 0,
        validity: str = "DAY",       # DAY / IOC / GTC
        tag: str = "AUTO",
    ) -> Dict:
        """Place a new order through Dhan API."""
        if self.paper_trading:
            logger.info(
                f"[PAPER] {transaction_type} {quantity} @ {price} | {security_id} | {product_type}"
            )
            return {
                "orderId": f"PAPER_{int(time.time())}",
                "status": "PAPER_TRADED",
                "securityId": security_id,
                "quantity": quantity,
                "price": price,
            }

        payload = {
            "dhanClientId": self.client_id,
            "transactionType": transaction_type,
            "exchangeSegment": exchange_segment,
            "productType": product_type,
            "orderType": order_type,
            "validity": validity,
            "securityId": security_id,
            "quantity": quantity,
            "disclosedQuantity": disclosed_quantity,
            "price": price,
            "triggerPrice": trigger_price,
            "afterMarketOrder": False,
            "correlationId": tag,
        }
        result = self._post("/orders", payload)
        logger.info(f"Order placed: {result}")
        return result

    def modify_order(self, order_id: str, order_type: str, quantity: int,
                     price: float, trigger_price: float = 0.0, validity: str = "DAY") -> Dict:
        """Modify an existing pending order."""
        if self.paper_trading:
            return {"status": "PAPER_MODIFIED", "orderId": order_id}

        payload = {
            "dhanClientId": self.client_id,
            "orderId": order_id,
            "orderType": order_type,
            "legName": "",
            "quantity": quantity,
            "price": price,
            "disclosedQuantity": 0,
            "triggerPrice": trigger_price,
            "validity": validity,
        }
        return self._post(f"/orders/{order_id}", payload)

    def cancel_order(self, order_id: str) -> Dict:
        """Cancel a pending order."""
        if self.paper_trading:
            return {"status": "PAPER_CANCELLED", "orderId": order_id}
        return self._delete(f"/orders/{order_id}")

    def get_order_by_id(self, order_id: str) -> Dict:
        return self._get(f"/orders/{order_id}")

    def get_order_list(self) -> List[Dict]:
        data = self._get("/orders")
        return data if isinstance(data, list) else []

    # ─────────────────────────────────────────────
    # POSITIONS & PORTFOLIO
    # ─────────────────────────────────────────────
    def get_positions(self) -> List[Dict]:
        """Get all open positions (intraday + overnight)."""
        data = self._get("/positions")
        return data if isinstance(data, list) else []

    def get_holdings(self) -> List[Dict]:
        """Get equity holdings (CNC positions)."""
        data = self._get("/holdings")
        return data if isinstance(data, list) else []

    def get_fund_limits(self) -> Dict:
        """Get available margin / fund limits."""
        return self._get("/fundlimit")

    # ─────────────────────────────────────────────
    # INSTRUMENT SEARCH
    # ─────────────────────────────────────────────
    def search_instruments(self, search_text: str) -> List[Dict]:
        """Search for instruments by name/symbol."""
        return self._get("/instruments/search", params={"search": search_text})

    # ─────────────────────────────────────────────
    # PRE-TRADE VALIDATION
    # ─────────────────────────────────────────────
    def validate_connection(self) -> tuple:
        """
        Verify that the Dhan API token is valid and the account is reachable.
        Calls /fundlimit — a lightweight endpoint that returns available margin.

        Returns:
            (True, fund_info_dict)   — token valid, account accessible
            (False, error_message)   — token expired or API unreachable

        Use this BEFORE placing any live order to avoid silent failures.
        """
        try:
            resp = self.session.get(
                f"{DHAN_BASE_URL}/fundlimit",
                timeout=8
            )
            if resp.status_code == 200:
                data = resp.json()
                avail = data.get("availabelBalance", data.get("availableBalance", "N/A"))
                logger.info(f"[Validation] ✅ Token valid | Available balance: ₹{avail}")
                return True, data

            elif resp.status_code == 401:
                msg = "Token expired (HTTP 401). Run update_token.py to refresh."
                logger.error(f"[Validation] ❌ {msg}")
                return False, msg

            elif resp.status_code == 429:
                msg = "Rate limited (HTTP 429). Wait 1–2 minutes before retrying."
                logger.warning(f"[Validation] ⚠️ {msg}")
                return False, msg

            else:
                msg = f"Unexpected HTTP {resp.status_code}: {resp.text[:100]}"
                logger.error(f"[Validation] ❌ {msg}")
                return False, msg

        except requests.exceptions.Timeout:
            msg = "API timeout — Dhan servers not responding within 8s"
            logger.error(f"[Validation] ❌ {msg}")
            return False, msg

        except requests.exceptions.ConnectionError:
            msg = "No internet connection or Dhan API unreachable"
            logger.error(f"[Validation] ❌ {msg}")
            return False, msg

        except Exception as e:
            msg = f"Validation error: {e}"
            logger.error(f"[Validation] ❌ {msg}")
            return False, msg

    def get_available_margin(self) -> float:
        """Shortcut: return available margin in ₹. Returns 0 on error."""
        ok, data = self.validate_connection()
        if ok and isinstance(data, dict):
            return float(data.get("availabelBalance", data.get("availableBalance", 0)) or 0)
        return 0.0
