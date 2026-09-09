"""Proteksi berlapis: cek kejanggalan SEBELUM kerugian terjadi.

Guard memeriksa berbagai kondisi abnormal dan memicu peringatan + aksi
sebelum saldo turun. Dipanggil di beberapa titik kritis di bot.py.
"""
import time


def build_guard_checks(rm, balance, market, position, conf):
    """Kembalikan daftar hasil cek. Tiap item: (level, pesan).
    level: "CRITICAL" (stop bot), "WARN" (peringatan), "INFO" (normal)."""
    checks = []
    price = market.get("price", 0)
    orderbook = market.get("orderbook", {})
    kl = market.get("klines_15m", [])

    # 1. Harga 0 / tidak valid
    if not price or price <= 0:
        checks.append(("CRITICAL", f"Harga BTC tidak valid: {price}"))

    # 2. Spread terlalu lebar (orderbook abnormal)
    try:
        bids = orderbook.get("b", [])
        asks = orderbook.get("a", [])
        if bids and asks:
            best_bid = float(bids[0][0])
            best_ask = float(asks[0][0])
            spread_pct = (best_ask - best_bid) / best_bid * 100 if best_bid else 0
            if spread_pct > 1.0:
                checks.append(("CRITICAL", f"Spread abnormal {spread_pct:.2f}% (>1%). Likuiditas hilang, orderbook mencurigakan."))
            elif spread_pct > 0.5:
                checks.append(("WARN", f"Spread lebar {spread_pct:.2f}% (normal <0.1%)."))
    except Exception:
        pass

    # 3. Harga melonjak/turun ekstrem antar candle (flash crash / pump)
    if len(kl) >= 2:
        try:
            prev_close = float(kl[-2]["close"])
            last_close = float(kl[-1]["close"])
            if prev_close:
                jump = (last_close - prev_close) / prev_close * 100
                if abs(jump) > 3.0:
                    if position == "long" and jump < 0:
                        checks.append(("CRITICAL", f"HARGA AMBROL {jump:.1f}% dalam 1 candle. JUAL SEGERA (posisi aktif)."))
                    else:
                        checks.append(("CRITICAL", f"Harga melonjak {jump:+.1f}% dalam 1 candle. Pasar tidak stabil, jangan entry."))
        except Exception:
            pass

    # 4. Volume kosong / tidak ada likuiditas
    try:
        vol = float(kl[-1]["volume"]) if kl else 0
        if vol <= 0:
            checks.append(("WARN", "Volume 0 — kemungkinan data pasar terputus."))
    except Exception:
        pass

    # 5. Confidence AI terlalu rendah untuk entry
    if position == "none" and conf < 50:
        checks.append(("INFO", f"Confidence AI rendah ({conf}) — sebaiknya tidak entry."))

    # 6. Saldo mendekati nol
    if balance <= 0:
        checks.append(("WARN", "Saldo 0 USDT — tidak bisa trading."))

    # 7. Cooldown / daily loss sudah di-handle risk_manager, info saja
    if rm.in_cooldown():
        checks.append(("INFO", "Bot dalam cooldown (3 loss beruntun)."))

    return checks


def evaluate(checks):
    """Kembalikan (should_block, alerts, warns)."""
    critical = [m for l, m in checks if l == "CRITICAL"]
    warns = [m for l, m in checks if l == "WARN"]
    return bool(critical), critical + warns, warns
