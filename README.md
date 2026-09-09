# Trading Bot

AI-driven crypto trading bot untuk Bybit Spot USDT. Scan seluruh pasar Bybit, analisis dengan DeepSeek AI, eksekusi otomatis dengan risk management ketat.

## Fitur

- Scan 538+ pair USDT di Bybit Spot
- Analisis multi-timeframe (15m/1h/4h)
- Support/resistance + volume profile + BTC context
- DeepSeek AI untuk keputusan entry/exit
- Trailing stop dinamis (0.5%-1.7%)
- Break-even otomatis (trigger di +0.5%)
- Time stop (4 jam)
- Circuit breaker (turun 3% dari puncak harian)
- Cooldown 6 jam setelah 3 loss berturut
- Daily max loss -1%
- Guard deteksi kejanggalan sebelum trade
- Dashboard web UI (port 8080)

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# edit .env, isi API key Bybit + AI
```

## Jalankan

```bash
# Start daemon + dashboard
./scripts/bg_start.sh

# Buka dashboard
http://localhost:8080

# Tekan START BOT di dashboard

# Stop
./scripts/bg_stop.sh
```

## Struktur

| File | Fungsi |
|------|--------|
| `bot.py` | Loop utama: scan → AI → entry → exit |
| `daemon.py` | Web server + dashboard + kelola bot |
| `scanner.py` | Scan pasar, scoring, enrichment |
| `ai_analyzer.py` | Prompt DeepSeek AI, parse hasil |
| `risk_manager.py` | Position sizing, trailing, break-even |
| `guard.py` | Deteksi kejanggalan sebelum trade |
| `bybit_client.py` | API client Bybit V5 |
| `candidate_memory.py` | Track kandidat, cooldown, retrigger |
| `state.py` | Shared state, history, export |
| `market_analyzer.py` | Analisis pasar on-demand |
| `scripts/` | Start/stop daemon |
| `riwayat_transaksi/` | Riwayat trade harian (JSON + TXT) |

## Konfigurasi

Semua di `.env`. Lihat `.env.example` untuk referensi lengkap.

Parameter AI & bot juga bisa diubah langsung dari dashboard (menu Pengaturan):
- Config AI (base URL, key, model)
- Pengaturan Bot (daily max loss, risk per trade, trailing stop, break-even, R/R, cooldown, time stop, scan interval, AI confidence min)

Perubahan berlaku setelah restart bot.

Penting:
- `DRY_RUN=1` → mode aman (tidak pakai uang asli)
- `DRY_RUN=0` → **LIVE, pakai dana asli**
- `AI_API_KEY` → kunci provider AI
- `BYBIT_API_KEY` / `BYBIT_API_SECRET` → kunci Bybit (permission Trade)

## Risk Management

- Risk per trade: 0.5% normal, 0.35% (conf 60-69), 0.25% (setelah 3 loss)
- Minimum R/R: 1.5x
- TP minimum: entry + (entry - SL) x 1.5 (pastikan profit > fee 0.2%)
- Circuit breaker: auto sell & stop kalau turun 3% dari puncak harian
- Daily max loss: -1%
