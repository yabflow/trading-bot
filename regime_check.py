"""Lightweight market regime check tanpa AI call.

Hanya baca BTCUSDT klines dan hitung:
- trend (bullish/bearish/neutral) dari EMA cross
- volatility (range%) 
- sideways detection (range sempit + trend neutral)

Dipakai di bot.py sebagai gate entry.
Cache 5 menit agar tidak hit Bybit setiap cycle.
"""
import os
import time

import bybit_client as bc


_LAST_CHECK = 0
_CACHE = {"regime": "unknown", "trend": "unknown", "volatility_pct": 0.0, "btc_price": 0.0}
_TTL_SEC = int(os.getenv("REGIME_CACHE_TTL", "300"))


def _fetch_btc_klines():
    """Ambil BTCUSDT 1h klines 50 candle."""
    try:
        r = bc.get_klines("BTCUSDT", interval="60", limit=50)
        candles = []
        for k in r.get("result", {}).get("list", []):
            candles.append({
                "open": float(k[1]),
                "high": float(k[2]),
                "low": float(k[3]),
                "close": float(k[4]),
                "volume": float(k[5]),
            })
        return candles
    except Exception:
        return []


def _ema(values, n):
    if len(values) < n:
        return None
    k = 2 / (n + 1)
    e = sum(values[:n]) / n
    for v in values[n:]:
        e = v * k + e * (1 - k)
    return e


def _classify(candles):
    if len(candles) < 25:
        return {"regime": "unknown", "trend": "unknown", "volatility_pct": 0.0, "btc_price": 0.0}
    closes = [c["close"] for c in candles]
    highs = [c["high"] for c in candles]
    lows = [c["low"] for c in candles]
    ema9 = _ema(closes, 9)
    ema21 = _ema(closes, 21)
    price = closes[-1]
    if ema9 and ema21:
        if ema9 > ema21 * 1.005:
            trend = "bullish"
        elif ema9 < ema21 * 0.995:
            trend = "bearish"
        else:
            trend = "neutral"
    else:
        trend = "unknown"
    recent_range = [(h - l) / l * 100 for h, l in zip(highs[-20:], lows[-20:])]
    volatility_pct = sum(recent_range) / len(recent_range) if recent_range else 0.0
    if trend == "neutral" and volatility_pct < 1.5:
        regime = "sideways"
    elif trend == "bullish" and volatility_pct > 3.0:
        regime = "bullish_volatile"
    elif trend == "bearish" and volatility_pct > 3.0:
        regime = "bearish_volatile"
    elif trend == "bullish":
        regime = "bullish"
    elif trend == "bearish":
        regime = "bearish"
    else:
        regime = "transitional"
    return {
        "regime": regime,
        "trend": trend,
        "volatility_pct": round(volatility_pct, 2),
        "btc_price": round(price, 2),
    }


def get_regime(force_refresh=False):
    """Return regime dict. Cache 5 menit."""
    global _LAST_CHECK
    now = time.time()
    if not force_refresh and (now - _LAST_CHECK) < _TTL_SEC:
        return _CACHE
    candles = _fetch_btc_klines()
    result = _classify(candles)
    _CACHE.update(result)
    _LAST_CHECK = now
    return _CACHE


def is_sideways(force_refresh=False):
    """Return True kalau BTC sideways."""
    r = get_regime(force_refresh=force_refresh)
    return r.get("regime") == "sideways"
