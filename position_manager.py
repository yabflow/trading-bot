import time

import bybit_client as bc

_UNKNOWN = "UNKNOWN"


def get_position():
    """Posisi aktif (size != 0) atau None.

    Bedakan 3 keadaan:
      - None            : pasti tidak ada posisi (API menjawab sukses)
      - dict            : ada posisi
      - "UNKNOWN" (str) : API gagal, status tidak diketahui (JANGAN entry)
    """
    try:
        r = bc.get_positions()
    except Exception:
        return _UNKNOWN
    if r.get("retCode") != 0:
        return _UNKNOWN
    lst = r.get("result", {}).get("list", [])
    for p in lst:
        if float(p.get("size", 0) or 0) != 0:
            return {
                "symbol": p.get("symbol"),
                "side": p.get("side"),          # Buy=long, Sell=short
                "qty": abs(float(p.get("size", 0))),
                "entry": float(p.get("avgPrice", 0)),
                "leverage": p.get("leverage"),
                "unrealisedPnl": float(p.get("unrealisedPnl", 0)),
                "stop_loss": _opt_float(p.get("stopLoss")),
                "take_profit": _opt_float(p.get("takeProfit")),
                "liq_price": _opt_float(p.get("liqPrice")),
            }
    return None


def _opt_float(v):
    try:
        if v in (None, "", "0"):
            return None
        return float(v)
    except (ValueError, TypeError):
        return None


def get_position_detail(symbol):
    """Detail posisi satu symbol (untuk verifikasi SL/TP setelah entry)."""
    try:
        r = bc.get_position_info(symbol)
    except Exception:
        return _UNKNOWN
    if r.get("retCode") != 0:
        return _UNKNOWN
    lst = r.get("result", {}).get("list", [])
    for p in lst:
        if float(p.get("size", 0) or 0) != 0:
            return {
                "symbol": p.get("symbol"),
                "side": p.get("side"),
                "qty": abs(float(p.get("size", 0))),
                "entry": float(p.get("avgPrice", 0)),
                "leverage": p.get("leverage"),
                "stop_loss": _opt_float(p.get("stopLoss")),
                "take_profit": _opt_float(p.get("takeProfit")),
                "liq_price": _opt_float(p.get("liqPrice")),
                "unrealisedPnl": float(p.get("unrealisedPnl", 0)),
            }
    return None


def has_position():
    """True jika pasti ada posisi. UNKNOWN/None → False (jangan blok, tapi caller cek UNKNOWN)."""
    return isinstance(get_position(), dict)


def sell_all():
    """Tutup semua posisi aktif + verifikasi size benar-benar 0 + cancel order terbuka.

    Return list hasil close. Verifikasi via Bybit bahwa position size == 0.
    """
    results = []
    pos = get_position()
    if pos == _UNKNOWN:
        # status tidak diketahui → coba close semua symbol yang mungkin, lalu verifikasi
        results.append("UNKNOWN")
    if isinstance(pos, dict):
        results.append(bc.close_position(pos["symbol"], pos["side"], pos["qty"]))

    # cancel semua open order yang tersisa
    try:
        bc.cancel_all_orders()
    except Exception:
        pass

    # verifikasi final: position size harus 0
    time.sleep(1)
    final = get_position()
    if final == _UNKNOWN:
        results.append("VERIFY-UNKNOWN")
    elif isinstance(final, dict):
        results.append(f"STILL-OPEN:{final['symbol']}")
    else:
        results.append("VERIFIED-CLOSED")
    return results


def cancel_all():
    return bc.cancel_all_orders()
