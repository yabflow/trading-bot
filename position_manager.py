import bybit_client as bc


def has_position():
    try:
        r = bc.get_positions()
        lst = r.get("result", {}).get("list", [])
        for p in lst:
            if float(p.get("qty", "0")) > 0:
                return True
        return False
    except Exception:
        return False


def sell_all():
    return bc.close_all_positions()


def cancel_all():
    return bc.cancel_all_orders()
