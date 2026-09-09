import sys
import urllib.request
import json

BASE_URL = "https://api.bybit.com"

def main():
    try:
        with urllib.request.urlopen(BASE_URL + "/v5/market/time", timeout=10) as r:
            data = json.loads(r.read())
        print(f"HEALTH OK: Bybit reachable (time: {data['result']['timeNano']})")
        return 0
    except Exception as e:
        print(f"HEALTH FAIL: {e}")
        return 1

if __name__ == "__main__":
    sys.exit(main())
