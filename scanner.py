"""Market-wide scanner untuk Bybit Spot USDT.

Scan semua pair USDT, deteksi kandidat berdasarkan kombinasi:
momentum, breakout, volume spike, trend, volatility.

Prinsip: "filter sampah, bukan filter peluang".
Threshold configurable via .env, tidak hard-code.
"""
import os
import time

import bybit_client as bc
from dotenv import load_dotenv

load_dotenv()

# --- configurable thresholds (via .env) ---
MIN_VOLUME_USD = float(os.getenv("SCAN_MIN_VOLUME_USD", "500000"))      # min turnover 24h
MIN_PRICE = float(os.getenv("SCAN_MIN_PRICE", "0.000001"))             # harga minimal
MAX_SPREAD_PCT = float(os.getenv("SCAN_MAX_SPREAD_PCT", "1.0"))        # spread maksimal
MIN_PRICE_CHANGE = float(os.getenv("SCAN_MIN_PRICE_CHANGE", "1.0"))    # min %24h untuk dipertimbangkan
MIN_VOLUME_SPIKE = float(os.getenv("SCAN_MIN_VOLUME_SPIKE", "1.5"))    # volume candle vs avg
TOP_CANDIDATES = int(os.getenv("SCAN_TOP_CANDIDATES", "30"))           # kandidat broad
AI_CANDIDATES = int(os.getenv("SCAN_AI_CANDIDATES", "10"))             # kirim ke AI
PRE_KLINE_LIMIT = int(os.getenv("SCAN_PRE_KLINE_LIMIT", "40"))         # klines hanya utk top N

TIMEFRAMES = ["5", "15", "60", "240"]  # 5m, 15m, 1h, 4h


def _is_usdt_pair(symbol):
    if not symbol.endswith("USDT"):
        return False
    base = symbol[:-4]
    # buang stablecoin dan token non-tradable sebagai base
    if base in ("USDC", "USDE", "USD", "EUR", "FDUSD", "TUSD", "DAI", "BUSD", "XAUT"):
        return False
    return True


def fetch_all_tickers():
    r = bc.get_all_tickers()
    return r.get("result", {}).get("list", [])


def _spread_pct(t):
    try:
        bid = float(t.get("bid1Price", 0))
        ask = float(t.get("ask1Price", 0))
        if bid <= 0 or ask <= 0:
            return 999.0
        return (ask - bid) / bid * 100
    except Exception:
        return 999.0


def _initial_filter(tickers):
    """Buang sampah: non-USDT, volume rendah, harga invalid, spread lebar."""
    out = []
    for t in tickers:
        sym = t.get("symbol", "")
        if not _is_usdt_pair(sym):
            continue
        try:
            price = float(t.get("lastPrice", 0))
            turnover = float(t.get("turnover24h", 0))
            if price < MIN_PRICE:
                continue
            if turnover < MIN_VOLUME_USD:
                continue
            if _spread_pct(t) > MAX_SPREAD_PCT:
                continue
            out.append({
                "symbol": sym,
                "price": price,
                "turnover24h": turnover,
                "volume24h": float(t.get("volume24h", 0)),
                "price24hPcnt": float(t.get("price24hPcnt", 0)),
                "high24h": float(t.get("highPrice24h", 0)),
                "low24h": float(t.get("lowPrice24h", 0)),
                "spread_pct": _spread_pct(t),
            })
        except (ValueError, TypeError):
            continue
    return out


def _fetch_klines(symbol, interval, limit=30):
    try:
        r = bc.get_klines(symbol, interval=interval, limit=limit)
        lst = r.get("result", {}).get("list", [])
        candles = []
        for k in lst:
            candles.append({
                "open": float(k[1]), "high": float(k[2]),
                "low": float(k[3]), "close": float(k[4]),
                "volume": float(k[5]),
            })
        # buang candle terakhir (masih forming) — hanya pakai closed candles
        if candles:
            candles.pop()
        return candles
    except Exception:
        return []


def _indicators(candles):
    if len(candles) < 5:
        return {}
    closes = [c["close"] for c in candles]
    vols = [c["volume"] for c in candles]
    price = closes[-1]

    # momentum: perubahan % dari N candle lalu
    mom_5 = (closes[-1] - closes[-6]) / closes[-6] * 100 if len(closes) >= 6 else 0

    # RSI 14
    rsi = None
    if len(closes) >= 15:
        gains, losses = [], []
        for i in range(1, len(closes)):
            d = closes[i] - closes[i - 1]
            gains.append(max(d, 0))
            losses.append(max(-d, 0))
        ag = sum(gains[-14:]) / 14
        al = sum(losses[-14:]) / 14
        rsi = 100 if al == 0 else 100 - (100 / (1 + (ag / al)))

    def ema(vals, n):
        if len(vals) < n:
            return None
        k = 2 / (n + 1)
        e = sum(vals[:n]) / n
        for v in vals[n:]:
            e = v * k + e * (1 - k)
        return e

    ema9 = ema(closes, 9)
    ema21 = ema(closes, 21)

    # volume spike: volume candle terakhir vs rata-rata
    avg_vol = sum(vols[:-1]) / max(len(vols) - 1, 1)
    vol_spike = vols[-1] / avg_vol if avg_vol > 0 else 0

    # breakout: close terakhir > high N candle sebelumnya (resistance)
    breakout = None
    if len(closes) >= 20:
        prev_high = max(c["high"] for c in candles[-20:-1])
        breakout = closes[-1] > prev_high

    # volatility: rentang (high-low) relatif
    recent = candles[-10:]
    avg_range = sum((c["high"] - c["low"]) / c["close"] for c in recent) / len(recent) * 100

    return {
        "price": price,
        "rsi": round(rsi, 1) if rsi is not None else None,
        "ema9": round(ema9, 4) if ema9 else None,
        "ema21": round(ema21, 4) if ema21 else None,
        "momentum_5": round(mom_5, 2),
        "vol_spike": round(vol_spike, 2),
        "breakout": bool(breakout),
        "avg_range_pct": round(avg_range, 2),
        "trend": "bullish" if ema9 and ema21 and ema9 > ema21 else ("bearish" if ema9 and ema21 else "netral"),
    }


def _score(c, ind):
    """Skor kombinasi: momentum + volume + trend + breakout + volatilitas.
    Bukan cuma %24h. Skor lebih tinggi = peluang teknikal lebih kuat."""
    score = 0.0
    reasons = []

    # 24h change (bobot sedang — bukan dominan)
    chg = c["price24hPcnt"]
    if chg > 0:
        score += min(chg, 15) * 1.0
    elif chg < -15:
        # oversold dalam, potensi reversal
        score += abs(chg) * 0.3

    # momentum 5 candle
    if ind.get("momentum_5", 0) > 0.5:
        score += min(ind["momentum_5"], 5) * 1.5
        reasons.append("momentum")

    # volume spike
    vs = ind.get("vol_spike", 0)
    if vs >= MIN_VOLUME_SPIKE:
        score += min(vs, 5) * 2.0
        reasons.append(f"vol-spike-{vs:.1f}x")

    # trend bullish (EMA9 > EMA21)
    if ind.get("trend") == "bullish":
        score += 3.0
        reasons.append("trend-up")

    # breakout
    if ind.get("breakout"):
        score += 5.0
        reasons.append("breakout")

    # RSI ideal (40-65 = momentum sehat belum overbought)
    rsi = ind.get("rsi")
    if rsi is not None and 40 <= rsi <= 65:
        score += 2.0
        reasons.append("rsi-sehat")
    elif rsi is not None and rsi < 30:
        score += 1.0
        reasons.append("oversold")

    # volatilitas sehat (tidak terlalu flat)
    ar = ind.get("avg_range_pct", 0)
    if 0.2 <= ar <= 3.0:
        score += 1.0

    return score, reasons


def _score_from_ticker(c):
    """Skor awal hanya dari data ticker (tanpa klines).
    Cepat, tanpa request tambahan. Buang mayoritas sampah di sini."""
    score = 0.0
    reasons = []
    chg = c["price24hPcnt"]
    if chg > 0:
        score += min(chg, 15) * 1.0
        if chg >= MIN_PRICE_CHANGE:
            reasons.append("up-24h")
    # turnover besar = likuiditas
    if c["turnover24h"] > 5_000_000:
        score += 2.0
        reasons.append("liquid")
    return score, reasons


def scan():
    """Scan penuh: filter ticker → skor awal → klines hanya untuk top N."""
    t0 = time.time()
    tickers = fetch_all_tickers()
    filtered = _initial_filter(tickers)

    # skor awal dari ticker saja (cepat, tanpa klines)
    pre = []
    for c in filtered:
        score, reasons = _score_from_ticker(c)
        if score > 0:
            pre.append((c, score, reasons))
    pre.sort(key=lambda x: x[1], reverse=True)

    # ambil klines HANYA untuk top PRE_KLINE (hemat request)
    pre_klines = pre[:PRE_KLINE_LIMIT]
    candidates = []
    for c, _, pre_reasons in pre_klines:
        candles_15 = _fetch_klines(c["symbol"], "15", limit=30)
        if len(candles_15) < 15:
            continue
        ind = _indicators(candles_15)
        if not ind:
            continue
        score, reasons = _score(c, ind)
        if score > 0:
            candidates.append({
                "symbol": c["symbol"],
                "price": c["price"],
                "price24hPcnt": c["price24hPcnt"],
                "turnover24h": c["turnover24h"],
                "score": round(score, 2),
                "reasons": reasons,
                "indicators": ind,
            })

    candidates.sort(key=lambda x: x["score"], reverse=True)
    top = candidates[:TOP_CANDIDATES]
    log = [
        f"SCAN: {len(tickers)} pairs scanned",
        f"FILTER: {len(filtered)} lolos filter awal",
        f"CANDIDATE: {len(candidates)} candidates, {len(top)} top-ranked",
    ]

    elapsed = time.time() - t0
    return {"log": log, "candidates": candidates, "top": top, "elapsed": round(elapsed, 2)}


def enrich_multi_timeframe(symbol):
    """Ambil klines multi-timeframe untuk candidate (untuk AI)."""
    data = {"symbol": symbol}
    tf_map = {"5": "5m", "15": "15m", "60": "1h", "240": "4h"}
    for tf, name in tf_map.items():
        candles = _fetch_klines(symbol, tf, limit=30)
        if candles:
            data[name] = {
                "closes": [c["close"] for c in candles[-20:]],
                "highs": [c["high"] for c in candles[-20:]],
                "lows": [c["low"] for c in candles[-20:]],
                "volumes": [c["volume"] for c in candles[-20:]],
                "trend": _indicators(candles).get("trend"),
                "rsi": _indicators(candles).get("rsi"),
                "vol_spike": _indicators(candles).get("vol_spike"),
            }
    return data


def _support_resistance(candles):
    """Hitung support/resistance dari swing high/low sederhana."""
    if len(candles) < 10:
        return {}
    highs = [c["high"] for c in candles]
    lows = [c["low"] for c in candles]
    price = candles[-1]["close"]
    # resistance = high tertinggi yang masih di atas harga
    resistances = sorted([h for h in highs if h > price], reverse=True)
    # support = low terendah yang masih di bawah harga
    supports = sorted([l for l in lows if l < price])
    return {
        "resistance": [round(r, 6) for r in resistances[:3]],
        "support": [round(s, 6) for s in supports[:3]],
    }


def _volume_profile(candles):
    """Volume naik/turun trend + breakout volume confirmation."""
    if len(candles) < 5:
        return {}
    vols = [c["volume"] for c in candles]
    recent_avg = sum(vols[-3:]) / 3 if len(vols) >= 3 else vols[-1]
    prev_avg = sum(vols[:-3]) / max(len(vols) - 3, 1)
    vol_trend = "naik" if recent_avg > prev_avg * 1.2 else ("turun" if recent_avg < prev_avg * 0.8 else "flat")
    return {"volume_trend": vol_trend, "recent_vol_avg": round(recent_avg, 2), "prev_vol_avg": round(prev_avg, 2)}


def get_btc_context():
    """Tren BTC keseluruhan (arus besar). Semua altcoin ikut BTC."""
    try:
        candles = _fetch_klines("BTCUSDT", "60", limit=30)
        if not candles:
            return {}
        ind = _indicators(candles)
        closes = [c["close"] for c in candles]
        chg_24h = (closes[-1] - closes[0]) / closes[0] * 100 if len(closes) >= 2 else 0
        return {
            "btc_price": closes[-1],
            "btc_trend": ind.get("trend"),
            "btc_rsi": ind.get("rsi"),
            "btc_change_24h": round(chg_24h, 2),
        }
    except Exception:
        return {}


def enrich_full(symbol, base_candidate):
    """Data lengkap untuk AI: multi-timeframe + S/R + volume profil + konteks BTC."""
    data = enrich_multi_timeframe(symbol)
    candles_15 = _fetch_klines(symbol, "15", limit=30)
    data["support_resistance"] = _support_resistance(candles_15)
    data["volume_profile"] = _volume_profile(candles_15)
    data["btc_context"] = get_btc_context()
    data["candidate"] = {
        "symbol": symbol,
        "price": base_candidate.get("price"),
        "price24hPcnt": base_candidate.get("price24hPcnt"),
        "score": base_candidate.get("score"),
        "reasons": base_candidate.get("reasons"),
        "indicators": base_candidate.get("indicators"),
    }
    return data


if __name__ == "__main__":
    result = scan()
    for l in result["log"]:
        print(l)
    print(f"Elapsed: {result['elapsed']}s")
    print(f"\nTop {min(5, len(result['top']))} kandidat:")
    for c in result["top"][:5]:
        print(f"  {c['symbol']} score={c['score']} 24h={c['price24hPcnt']}% reasons={c['reasons']}")
