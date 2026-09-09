import json
import os
import urllib.request

from dotenv import load_dotenv

load_dotenv()

AI_BASE_URL = os.getenv("AI_BASE_URL", "https://api.thirtystore.com/v1")
AI_API_KEY = os.getenv("AI_API_KEY", "")
AI_MODEL = os.getenv("AI_MODEL", "thirty/deepseek-v4-pro-0813")

PROMPT = """Kamu analis trading crypto (Bybit Spot) berpengalaman. Analisis SATU kandidat coin.

Data kandidat (lengkap):
{market_data}

Posisi saat ini: {position}

Analisis yang WAJIB dilakukan:
1. TREND: arah tren di 15m, 1h, 4h. Apakah searah atau bertentangan?
2. KONTEKS BTC: apakah BTC (arus besar) mendukung? Jika BTC bearish, altcoin biasanya ikut turun.
3. SUPPORT/RESISTANCE: apakah harga dekat resistance (risiko ditolak) atau baru breakout support?
4. VOLUME: apakah volume naik mendukung pergerakan? Breakout tanpa volume = palsu.
5. RISK/REWARD: apakah potensi profit >= 1.5x risiko? Hitung dari entry ke TP vs entry ke SL.

CONTOH analisis BAGUS (BUY layak):
"BTC bullish, SOLUSDT 15m/1h/4h bullish searah, baru breakout resistance 105.2 dengan volume spike 3x, RSI 58 sehat. Entry 105.5, SL 103.5 (di bawah support), TP 109.5 (R/R 2.0)."

CONTOH analisis BURUK (harus HOLD):
"harga naik 10% tapi BTC bearish, 1h/4h masih bearish melawan arus, volume turun, dekat resistance kuat. Risiko ditolak tinggi."

KEPUTUSAN:
- BUY hanya jika: trend searah (multi-TF), BTC mendukung, volume konfirmasi, R/R >= 1.5.
- HOLD jika: keraguan, breakout belum confirmed, atau melawan arus BTC.
- JANGAN memaksakan entry hanya karena harga naik.

Balas HANYA JSON (tanpa teks lain):
{{"action":"buy"|"hold","confidence":0-100,"entry":angka,"stop_loss":angka,"take_profit":angka,"setup_type":"breakout|momentum|pullback|trend_continuation|volume_spike|other","reason":"satu kalimat"}}"""


def analyze_candidate(candidate_data, position="none"):
    return analyze(candidate_data, position)


def analyze(market_data, position):
    prompt = PROMPT.format(market_data=json.dumps(market_data, ensure_ascii=False), position=position)
    body = json.dumps({
        "model": AI_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.3,
        "max_tokens": 300,
    }).encode()
    req = urllib.request.Request(
        AI_BASE_URL + "/chat/completions",
        data=body,
        headers={
            "Authorization": "Bearer " + AI_API_KEY,
            "Content-Type": "application/json",
            "User-Agent": "TradingBot/1.0",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        resp = json.loads(r.read())
    content = resp["choices"][0]["message"]["content"]
    return _parse_json(content)


def _parse_json(text):
    text = text.strip()
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("AI response bukan JSON")
    return json.loads(text[start:end + 1])


if __name__ == "__main__":
    r = analyze({"harga": 78000, "rsi": 55}, "none")
    print(r)