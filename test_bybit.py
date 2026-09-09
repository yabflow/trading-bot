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
        t = bc.get_ticker("BTCUSDT")
        price = t["result"]["list"][0]["lastPrice"]
        results.append(("BTCUSDT futures market data", "OK"))
        print(f"BTCUSDT lastPrice: {price}")
    except Exception:
        results.append(("BTCUSDT futures market data", "FAIL"))

    results.append(("Trading orders", "NOT USED"))

    _print(results)
    return 0


def _print(results):
    for k, v in results:
        print(f"{k}: {v}")


if __name__ == "__main__":
    sys.exit(main())
