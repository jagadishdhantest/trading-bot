import os, requests, logging
from dotenv import load_dotenv

load_dotenv()

DHAN_BASE_URL = "https://api.dhan.co"

class DhanTrader:
    def __init__(self):
        self.access_token = os.getenv("DHAN_ACCESS_TOKEN")
        self.client_id    = os.getenv("DHAN_CLIENT_ID")
        self.headers = {
            "access-token" : self.access_token,
            "client-id"    : self.client_id,
            "Content-Type" : "application/json",
        }
        if not self.access_token or not self.client_id:
            raise ValueError("❌ DHAN_ACCESS_TOKEN and DHAN_CLIENT_ID must be set in .env")

    # ── Place order (market for BUY, limit for SELL) ─────
    def place_order(self, action, symbol, exchange, quantity, price, sl, target1, target2):

        # Determine transaction type
        transaction_type = "BUY" if action == "BUY" else "SELL"

        # Market order for BUY, Limit for SELL
        order_type  = "MARKET" if action == "BUY" else "LIMIT"
        limit_price = 0 if action == "BUY" else round(price, 2)

        # Dhan exchange segment mapping
        exchange_map = {
            "NSE": "NSE_EQ",
            "BSE": "BSE_EQ",
            "NFO": "NSE_FNO",
        }
        exchange_segment = exchange_map.get(exchange.upper(), "NSE_EQ")

        # Get security ID for the symbol
        security_id = self._get_security_id(symbol, exchange)

        payload = {
            "dhanClientId"     : self.client_id,
            "transactionType"  : transaction_type,
            "exchangeSegment"  : exchange_segment,
            "productType"      : "INTRADAY",          # MIS — auto squares off at 3:15 PM
            "orderType"        : order_type,
            "validity"         : "DAY",
            "tradingSymbol"    : symbol.upper(),
            "securityId"       : security_id,
            "quantity"         : quantity,
            "price"            : limit_price,
            "triggerPrice"     : 0,
            "disclosedQuantity": 0,
            "afterMarketOrder" : False,
            "boProfitValue"    : 0,
            "boStopLossValue"  : 0,
        }

        logging.info(f"Placing order: {payload}")
        response = requests.post(
            f"{DHAN_BASE_URL}/orders",
            headers = self.headers,
            json    = payload,
            timeout = 10,
        )
        response.raise_for_status()
        result = response.json()
        logging.info(f"Order result: {result}")

        # Place SL order immediately after entry
        if result.get("orderId") and sl > 0:
            self._place_sl_order(
                symbol          = symbol,
                security_id     = security_id,
                exchange_segment= exchange_segment,
                transaction_type= "SELL" if action == "BUY" else "BUY",
                quantity        = quantity,
                sl_price        = sl,
            )

        return result

    # ── Place Stop Loss order ────────────────────────────
    def _place_sl_order(self, symbol, security_id, exchange_segment,
                        transaction_type, quantity, sl_price):
        payload = {
            "dhanClientId"     : self.client_id,
            "transactionType"  : transaction_type,
            "exchangeSegment"  : exchange_segment,
            "productType"      : "INTRADAY",
            "orderType"        : "STOP_LOSS",
            "validity"         : "DAY",
            "tradingSymbol"    : symbol.upper(),
            "securityId"       : security_id,
            "quantity"         : quantity,
            "price"            : round(sl_price * 0.995, 2),  # limit slightly below SL
            "triggerPrice"     : round(sl_price, 2),
            "disclosedQuantity": 0,
            "afterMarketOrder" : False,
            "boProfitValue"    : 0,
            "boStopLossValue"  : 0,
        }
        try:
            r = requests.post(f"{DHAN_BASE_URL}/orders",
                              headers=self.headers, json=payload, timeout=10)
            r.raise_for_status()
            logging.info(f"SL order placed: {r.json()}")
        except Exception as e:
            logging.error(f"SL order failed: {e}")

    # ── Get security ID from Dhan symbol search ──────────
    def _get_security_id(self, symbol, exchange):
        # Dhan requires numeric securityId
        # You can hardcode frequently traded stocks here for speed
        # Or use Dhan's instrument CSV for mapping
        static_map = {
            "TRIVENI"            : "4506",
            "TRANSFORMERSINDIA"  : "10604",
            "GROWW"              : "11915",    # update with correct IDs
        }
        sid = static_map.get(symbol.upper())
        if sid:
            return sid

        # Fallback: search via Dhan API
        try:
            r = requests.get(
                f"{DHAN_BASE_URL}/instruments",
                headers = self.headers,
                params  = {"tradingSymbol": symbol.upper(), "exchangeSegment": "NSE_EQ"},
                timeout = 5,
            )
            instruments = r.json()
            if instruments:
                return str(instruments[0].get("securityId", "0"))
        except Exception as e:
            logging.error(f"Security ID lookup failed: {e}")

        return "0"

    # ── Get current positions ────────────────────────────
    def get_positions(self):
        r = requests.get(f"{DHAN_BASE_URL}/positions",
                         headers=self.headers, timeout=10)
        r.raise_for_status()
        return r.json()

    # ── Cancel all open orders ───────────────────────────
    def cancel_all_orders(self):
        r = requests.get(f"{DHAN_BASE_URL}/orders",
                         headers=self.headers, timeout=10)
        orders = r.json()
        for order in orders:
            if order.get("orderStatus") in ("PENDING", "TRANSIT"):
                oid = order["orderId"]
                requests.delete(f"{DHAN_BASE_URL}/orders/{oid}",
                                headers=self.headers, timeout=5)
                logging.info(f"Cancelled order {oid}")
