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
        if balance.get("retCode") == 0:
            results.append(("Account access", "OK"))
        else:
            results.append(("Account access", f"FAIL: retCode={balance.get('retCode')}"))
    except Exception as e:
        results.append(("Account access", "FAIL"))

    results.append(("Trading orders", "NOT USED"))

    _print(results)
    return 0


def _print(results):
    for k, v in results:
        print(f"{k}: {v}")


if __name__ == "__main__":
    sys.exit(main())