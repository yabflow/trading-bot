import hashlib
import hmac
import json
import os
import time
import urllib.parse
import urllib.request

from dotenv import load_dotenv

load_dotenv()

API_KEY = os.getenv("BYBIT_API_KEY", "")
API_SECRET = os.getenv("BYBIT_API_SECRET", "")
BASE_URL = os.getenv("BYBIT_BASE_URL", "https://api.bybit.com")
SYMBOL = os.getenv("TRADING_SYMBOL", "BTCUSDT")


def _public_request(path, params=None):
    qs = urllib.parse.urlencode(params or {})
    url = BASE_URL + path + ("?" + qs if qs else "")
    with urllib.request.urlopen(url, timeout=10) as r:
        return json.loads(r.read())


def _signed_request(path, params=None, body=None):
    params = dict(params or {})
    timestamp = str(int(time.time() * 1000))
    recv_window = "10000"
    if params:
        qs = urllib.parse.urlencode(sorted(params.items()))
    else:
        qs = ""
    if body:
        qs += ("&" if qs else "") + urllib.parse.urlencode(sorted(body.items()))
    sign_str = timestamp + API_KEY + recv_window + qs
    signature = hmac.new(API_SECRET.encode(), sign_str.encode(), hashlib.sha256).hexdigest()
    url = BASE_URL + path + ("?" + qs if qs else "")
    headers = {
        "X-BAPI-API-KEY": API_KEY,
        "X-BAPI-TIMESTAMP": timestamp,
        "X-BAPI-RECV-WINDOW": recv_window,
        "X-BAPI-SIGN": signature,
        "Content-Type": "application/json",
    }
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(url, headers=headers, data=data, method="POST" if body else None)
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read())


def get_server_time():
    return _public_request("/v5/market/time")


def get_ticker(symbol=None):
    sym = symbol or SYMBOL
    return _public_request("/v5/market/tickers", {"category": "spot", "symbol": sym})


def get_all_tickers():
    """Semua ticker spot dalam 1 request (batch)."""
    return _public_request("/v5/market/tickers", {"category": "spot"})


def get_orderbook(symbol=None, limit=5):
    sym = symbol or SYMBOL
    return _public_request("/v5/market/orderbook", {"category": "spot", "symbol": sym, "limit": limit})


def get_klines(symbol=None, interval="15", limit=50):
    sym = symbol or SYMBOL
    return _public_request("/v5/market/kline", {"category": "spot", "symbol": sym, "interval": interval, "limit": limit})


def get_wallet_balance():
    return _signed_request("/v5/account/wallet-balance", {"accountType": "UNIFIED"})


def get_positions():
    return _signed_request("/v5/position/list", {"category": "spot"})


def get_open_orders():
    return _signed_request("/v5/order/list", {"category": "spot", "symbol": SYMBOL, "orderStatus": "New"})


def create_order(side, qty, order_type="Market", symbol=None):
    sym = symbol or SYMBOL
    body = {
        "category": "spot",
        "symbol": sym,
        "side": side,
        "orderType": order_type,
        "qty": str(qty),
        "timeInForce": "IOC" if order_type == "Market" else "GTC",
    }
    return _signed_request("/v5/order/create", body=body)


def cancel_order(order_id):
    body = {"category": "spot", "symbol": SYMBOL, "orderId": order_id}
    return _signed_request("/v5/order/cancel", body=body)


def cancel_all_orders():
    body = {"category": "spot", "symbol": SYMBOL}
    return _signed_request("/v5/order/cancel-all", body=body)


def close_all_positions():
    positions = get_positions()
    result = positions.get("result", {})
    list_data = result.get("list", [])
    closed = []
    for pos in list_data:
        symbol = pos.get("symbol", "")
        side = pos.get("side", "")
        qty = pos.get("qty", "0")
        if float(qty) > 0:
            close_side = "Sell" if side == "Buy" else "Buy"
            r = create_order(close_side, qty)
            closed.append({"symbol": symbol, "side": side, "qty": qty, "close_side": close_side, "result": r.get("retCode")})
    return closed


def get_account_info():
    return _signed_request("/v5/account/info", {"accountType": "UNIFIED"})


def get_pnl_history(days=1):
    return _signed_request("/v5/account/deal-records", {"category": "spot", "symbol": SYMBOL, "limit": 100})
