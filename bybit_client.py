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
CATEGORY = "linear"  # USDT perpetual futures


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
    return _public_request("/v5/market/tickers", {"category": CATEGORY, "symbol": sym})


def get_all_tickers():
    """Semua ticker linear futures dalam 1 request."""
    return _public_request("/v5/market/tickers", {"category": CATEGORY})


def get_orderbook(symbol=None, limit=5):
    sym = symbol or SYMBOL
    return _public_request("/v5/market/orderbook", {"category": CATEGORY, "symbol": sym, "limit": limit})


def get_klines(symbol=None, interval="15", limit=50):
    sym = symbol or SYMBOL
    return _public_request("/v5/market/kline", {"category": CATEGORY, "symbol": sym, "interval": interval, "limit": limit})


def get_wallet_balance():
    r = _signed_request("/v5/account/wallet-balance", {"accountType": "UNIFIED"})
    lst = r.get("result", {}).get("list", [])
    if lst:
        item = lst[0]
        return float(item.get("totalWalletBalance", 0) or item.get("totalEquity", 0) or 0)
    return 0.0


def get_positions():
    return _signed_request("/v5/position/list", {"category": CATEGORY, "settleCoin": "USDT"})


def get_open_orders():
    return _signed_request("/v5/order/realtime", {"category": CATEGORY, "symbol": SYMBOL})


def get_instruments_info(symbol=None):
    """Info instrument (min order qty, qty step, price precision)."""
    sym = symbol or SYMBOL
    return _public_request("/v5/market/instruments-info", {"category": CATEGORY, "symbol": sym})


def set_leverage(leverage, symbol=None):
    sym = symbol or SYMBOL
    body = {"category": CATEGORY, "symbol": sym, "buyLeverage": str(leverage), "sellLeverage": str(leverage)}
    return _signed_request("/v5/position/set-leverage", body=body)


def create_order(side, qty, symbol=None, order_type="Market", stop_loss=None, take_profit=None, reduce_only=False):
    """side: "Buy" (long) / "Sell" (short). qty dalam kontrak (base asset)."""
    sym = symbol or SYMBOL
    body = {
        "category": CATEGORY,
        "symbol": sym,
        "side": side,
        "orderType": order_type,
        "qty": str(qty),
        "positionIdx": 0,          # one-way mode: 1 posisi aktif
        "timeInForce": "IOC" if order_type == "Market" else "GTC",
    }
    if stop_loss is not None:
        body["stopLoss"] = _round_price(stop_loss, sym)
    if take_profit is not None:
        body["takeProfit"] = _round_price(take_profit, sym)
    if reduce_only:
        body["reduceOnly"] = True
    return _signed_request("/v5/order/create", body=body)


def set_trading_stop(symbol, side, stop_loss=None, take_profit=None):
    """Update SL/TP exchange-side untuk posisi aktif (trailing via move SL)."""
    body = {
        "category": CATEGORY,
        "symbol": symbol,
        "positionIdx": 0,
    }
    if stop_loss is not None:
        body["stopLoss"] = _round_price(stop_loss, symbol)
    if take_profit is not None:
        body["takeProfit"] = _round_price(take_profit, symbol)
    return _signed_request("/v5/position/trading-stop", body=body)


def close_position(symbol, side, qty):
    """Tutup posisi penuh dengan market order berlawanan arah."""
    close_side = "Sell" if side == "Buy" else "Buy"
    body = {
        "category": CATEGORY,
        "symbol": symbol,
        "side": close_side,
        "orderType": "Market",
        "qty": str(qty),
        "positionIdx": 0,
        "timeInForce": "IOC",
        "reduceOnly": True,
    }
    return _signed_request("/v5/order/create", body=body)


def cancel_order(order_id):
    body = {"category": CATEGORY, "symbol": SYMBOL, "orderId": order_id}
    return _signed_request("/v5/order/cancel", body=body)


def cancel_all_orders():
    body = {"category": CATEGORY, "symbol": SYMBOL}
    return _signed_request("/v5/order/cancel-all", body=body)


def get_account_info():
    return _signed_request("/v5/account/info", {"accountType": "UNIFIED"})


def _round_price(price, symbol):
    """Round harga ke tick size yang benar (Bybit butuh precision tepat)."""
    try:
        info = get_instruments_info(symbol)
        items = info.get("result", {}).get("list", [])
        if items:
            tick = items[0].get("priceFilter", {}).get("tickSize", "0.01")
            dp = _decimals(tick)
            return str(round(float(price), dp))
    except Exception:
        pass
    return str(price)


def _decimals(tick):
    tick = str(tick).rstrip("0")
    if "." not in tick:
        return 0
    return len(tick.split(".")[1])


def round_qty(qty, symbol=None):
    """Round qty ke qtyStep & pastikan >= minOrderQty."""
    sym = symbol or SYMBOL
    try:
        info = get_instruments_info(sym)
        items = info.get("result", {}).get("list", [])
        if items:
            lot = items[0].get("lotSizeFilter", {})
            qty_step = lot.get("qtyStep", "1")
            min_qty = float(lot.get("minOrderQty", "0"))
            dp = _decimals(qty_step)
            q = round(float(qty), dp)
            if q < min_qty:
                q = min_qty
            return q
    except Exception:
        pass
    return qty
