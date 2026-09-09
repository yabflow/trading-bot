import sys

import bybit_client as bc


def main():
    results = []

    try:
        bc.get_server_time()
        results.append(("API connection", "OK"))
    except Exception:
        results.append(("API connection", "FAIL"))
        _print(results)
        return 1

    try:
        balance = bc.get_wallet_balance()
        if balance >= 0:
            results.append(("Account access", "OK"))
            print(f"Wallet balance: {balance} USDT")
        else:
            results.append(("Account access", "FAIL"))
    except Exception as e:
        results.append(("Account access", f"FAIL: {e}"))

    try:
        pos = bc.get_positions()
        if pos.get("retCode") == 0:
            results.append(("Position list (futures)", "OK"))
        else:
            results.append(("Position list (futures)", f"FAIL: retCode={pos.get('retCode')}"))
    except Exception as e:
        results.append(("Position list (futures)", f"FAIL: {e}"))

    results.append(("Trading orders", "NOT USED"))

    _print(results)
    return 0


def _print(results):
    for k, v in results:
        print(f"{k}: {v}")


if __name__ == "__main__":
    sys.exit(main())