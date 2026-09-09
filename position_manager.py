import bybit_client as bc


def has_position():
    try:
        return get_position() is not None
    except Exception:
        return False


def get_position():
    """Posisi aktif (size != 0). Kembalikan dict atau None."""
    try:
        r = bc.get_positions()
        lst = r.get("result", {}).get("list", [])
        for p in lst:
            if float(p.get("size", 0) or 0) != 0:
                return {
                    "symbol": p.get("symbol"),
                    "side": p.get("side"),          # Buy=long, Sell=short
                    "qty": float(p.get("size", 0)),
                    "entry": float(p.get("avgPrice", 0)),
                    "leverage": p.get("leverage"),
                    "unrealisedPnl": float(p.get("unrealisedPnl", 0)),
                }
        return None
    except Exception:
        return None


def sell_all():
    """Tutup semua posisi aktif (long atau short)."""
    pos = get_position()
    if pos is None:
        return []
    return [bc.close_position(pos["symbol"], pos["side"], pos["qty"])]


def cancel_all():
    return bc.cancel_all_orders()
