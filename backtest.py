"""Backtest scanner: ukur akurasi deteksi kandidat di data historis.

Tanpa AI (murah): jalankan scanner di data klines historis, cek apakah
kandidat yang terdeteksi (momentum/breakout/volume spike) benar-benar
naik dalam N candle berikutnya.

Ukuran: "hit rate" = % kandidat yang naik > threshold setelah terdeteksi.
"""
import time

import scanner


def backtest_scan(horizon_candles=6, gain_threshold=0.5, sample_size=15):
    """Untuk tiap kandidat top saat ini, lihat apakah sinyal historisnya
    valid: ambil klines, cek momentum/breakout di titik N candle lalu,
    lalu lihat hasil N candle setelahnya."""
    result = scanner.scan()
    top = result.get("top", [])[:sample_size]
    print(f"Backtest {len(top)} kandidat top, horizon {horizon_candles} candle, target gain {gain_threshold}%")

    hits = 0
    total = 0
    details = []
    for c in top:
        sym = c["symbol"]
        try:
            candles = scanner._fetch_klines(sym, "15", limit=60)
            if len(candles) < horizon_candles * 2 + 5:
                continue
            # titik entry = N candle sebelum terakhir
            entry_idx = len(candles) - horizon_candles - 1
            entry_price = candles[entry_idx]["close"]
            future_high = max(cd["high"] for cd in candles[entry_idx + 1:])
            gain = (future_high - entry_price) / entry_price * 100
            total += 1
            if gain >= gain_threshold:
                hits += 1
            details.append((sym, round(gain, 2)))
        except Exception as e:
            continue

    hit_rate = (hits / total * 100) if total else 0
    print(f"\nHasil: {hits}/{total} naik >= {gain_threshold}% dalam {horizon_candles} candle")
    print(f"Hit rate: {hit_rate:.1f}%")
    print("\nDetail gain tiap kandidat (forward {horizon_candles} candle):")
    for sym, gain in details:
        print(f"  {sym}: {gain:+.2f}%")
    return hit_rate


if __name__ == "__main__":
    backtest_scan()
