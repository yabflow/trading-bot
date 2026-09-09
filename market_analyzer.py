"""Analisis pasar on-demand untuk dashboard.

Fetch data pasar Bybit + minta ringkasan informatif dari AI.
Read-only, tidak menyentuh order.
"""
import time

import bybit_client as bc
import ai_analyzer as ai


def _indicators(klines):
    """Hitung indikator sederhana dari klines (OHLC list of dict)."""
    if not klines:
        return {}
    closes = [float(k["close"]) for k in klines]
    highs = [float(k["high"]) for k in klines]
    lows = [float(k["low"]) for k in klines]
    vols = [float(k["volume"]) for k in klines]
    price = closes[-1]

    # RSI 14
    rsi = None
    if len(closes) >= 15:
        gains, losses = [], []
        for i in range(1, len(closes)):
            d = closes[i] - closes[i - 1]
            gains.append(max(d, 0))
            losses.append(max(-d, 0))
        avg_gain = sum(gains[-14:]) / 14
        avg_loss = sum(losses[-14:]) / 14
        if avg_loss == 0:
            rsi = 100.0
        else:
            rs = avg_gain / avg_loss
            rsi = 100 - (100 / (1 + rs))

    # EMA 9 & 21
    def ema(values, n):
        if len(values) < n:
            return None
        k = 2 / (n + 1)
        e = sum(values[:n]) / n
        for v in values[n:]:
            e = v * k + e * (1 - k)
        return e

    ema9 = ema(closes, 9)
    ema21 = ema(closes, 21)

    # 24h change
    change_24h = None
    if len(closes) >= 2:
        change_24h = (closes[-1] - closes[0]) / closes[0] * 100

    # volume trend
    vol_avg = sum(vols[-10:]) / 10 if len(vols) >= 10 else sum(vols) / max(len(vols), 1)
    vol_last = vols[-1] if vols else 0

    return {
        "price": price,
        "rsi": round(rsi, 1) if rsi is not None else None,
        "ema9": round(ema9, 1) if ema9 else None,
        "ema21": round(ema21, 1) if ema21 else None,
        "change_pct": round(change_24h, 2) if change_24h is not None else None,
        "high": max(highs) if highs else None,
        "low": min(lows) if lows else None,
        "volume_last": vol_last,
        "volume_avg": round(vol_avg, 2),
        "trend": "bullish" if ema9 and ema21 and ema9 > ema21 else ("bearish" if ema9 and ema21 else "netral"),
    }


def get_analysis():
    """Kembalikan dict: indikator + ringkasan AI + rekomendasi."""
    try:
        ticker = bc.get_ticker()
        price = float(ticker["result"]["list"][0]["lastPrice"])
    except Exception:
        return {"error": "gagal ambil harga"}

    try:
        klines = bc.get_klines()
        candles = []
        for k in klines.get("result", {}).get("list", [])[-50:]:
            candles.append({"open": k[1], "high": k[2], "low": k[3],
                            "close": k[4], "volume": k[5]})
    except Exception:
        candles = []

    ind = _indicators(candles)
    ind["price"] = price

    # tanya AI untuk ringkasan informatif
    ai_summary = None
    try:
        prompt_data = {"harga": price, "indikator": ind, "klines_15m": candles[-10:]}
        res = ai.analyze(prompt_data, "market-overview")
        ai_summary = res
    except Exception as e:
        ai_summary = {"error": str(e)}

    return {
        "indicators": ind,
        "ai": ai_summary,
        "time": time.strftime("%H:%M:%S"),
    }
