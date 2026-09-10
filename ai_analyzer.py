import json
import os
import urllib.request

from dotenv import load_dotenv

load_dotenv()

AI_BASE_URL = os.getenv("AI_BASE_URL", "https://api.thirtystore.com/v1")
AI_API_KEY = os.getenv("AI_API_KEY", "")
AI_MODEL = os.getenv("AI_MODEL", "thirty/deepseek-v4-pro-0813")

PROMPT = """Kamu analis trading crypto (Bybit USDT Perpetual Futures) berpengalaman. Analisis SATU kandidat coin.

Data kandidat (lengkap):
{market_data}

Posisi saat ini: {position}

Analisis yang WAJIB dilakukan:
1. TREND: arah tren di 15m, 1h, 4h. Searah sempurna tidak wajib — yang penting TF dominan (1h/4h) tidak bertentangan keras dengan arah entry.
2. KONTEKS BTC: apakah BTC (arus besar) mendukung? BTC flat/netral masih boleh entry jika setup coin kuat.
3. SUPPORT/RESISTANCE: apakah harga dekat resistance (risiko ditolak) atau dekat support (potensi bounce)?
4. VOLUME: volume naik mendukung. Volume flat boleh jika ada katalis lain (breakout, momentum kuat).
5. RISK/REWARD: apakah potensi profit >= 1.5x risiko? Hitung dari entry ke TP vs entry ke SL.

Arah trading (LONG/SHORT):
- long: masuk beli, untung jika harga NAIK. SL di bawah entry, TP di atas entry.
- short: masuk jual, untung jika harga TURUN. SL di atas entry, TP di bawah entry.
- Pilih short hanya jika tren dominan bearish & BTC mendukung penurunan.

CONTOH analisis BAGUS (long layak):
"BTC netral, SOLUSDT 15m/1h bullish, 4h netral, harga bounce dari support 105.2 dengan volume naik, RSI 52 sehat. Entry 105.5, SL 103.5, TP 109.5 (R/R 2.0)."

CONTOH analisis BAGUS (short layak):
"BTC bearish, ETHUSDT 15m/1h/4h bearish, breakdown support 3000 volume naik, RSI 35. Entry 2995, SL 3020, TP 2910 (R/R 3.4)."

CONTOH analisis BURUK (harus hold):
"harga naik 10% tapi BTC bearish kuat, 1h/4h bearish keras melawan arus, volume turun, tepat di resistance kuat. Risiko ditolak tinggi."

CONFIDENCE (0-100) — wajib isi dengan jujur, jangan selalu 0:
- 80-100: setup sangat kuat, multi-TF searah, volume konfirmasi, R/R bagus.
- 60-79: setup layak, mayoritas faktor mendukung, R/R memadai.
- 40-59: setup marginal, ada konflik kecil antar TF.
- 0-39: setup lemah/tidak jelas → hold.

KEPUTUSAN:
- Entry jika setup layak (confidence >= 60) dan R/R >= 1.5.
- Hold jika confidence < 60, R/R < 1.5, atau melawan arus BTC keras.
- Jangan tuntut sempurna — cari setup yang LAYAK dengan R/R memadai.
- Lebih baik tidak trading daripada setup jelek, tapi jangan melewatkan setup bagus hanya karena ada konflik kecil.

Balas HANYA JSON (tanpa teks lain):
{{"action":"long"|"short"|"hold","confidence":0-100,"entry":angka,"stop_loss":angka,"take_profit":angka,"setup_type":"breakout|momentum|pullback|trend_continuation|volume_spike|other","reason":"satu kalimat"}}"""


_ACTION_MAP = {"buy": "long", "sell": "short", "long": "long", "short": "short"}


def _normalize(res):
    """Normalisasi & validasi hasil AI. Invalid/ambiguous → hold.

    Tidak pernah fallback diam-diam ke long.
    """
    if not isinstance(res, dict):
        return {"action": "hold", "confidence": 0, "reason": "AI response invalid"}
    a = str(res.get("action", "hold")).lower().strip()
    action = _ACTION_MAP.get(a, "hold")
    try:
        conf = int(res.get("confidence", 0))
    except (ValueError, TypeError):
        conf = 0
    if not (0 <= conf <= 100):
        conf = 0
    try:
        entry = float(res.get("entry", 0))
        stop_loss = float(res.get("stop_loss", 0))
        take_profit = float(res.get("take_profit", 0))
    except (ValueError, TypeError):
        entry = stop_loss = take_profit = 0.0

    out = {
        "action": action,
        "confidence": conf,
        "entry": entry,
        "stop_loss": stop_loss,
        "take_profit": take_profit,
        "setup_type": res.get("setup_type", "other"),
        "reason": res.get("reason", ""),
    }
    # arah tidak valid → hold (fail-safe)
    if action == "long":
        if entry <= 0 or not (stop_loss < entry < take_profit):
            out["action"] = "hold"
    elif action == "short":
        if entry <= 0 or not (take_profit < entry < stop_loss):
            out["action"] = "hold"
    else:
        out["confidence"] = 0
    return out


def analyze_candidate(candidate_data, position="none"):
    try:
        res = analyze(candidate_data, position)
    except Exception:
        return {"action": "hold", "confidence": 0, "reason": "AI error"}
    return _normalize(res)


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


def _safe_parse(text):
    """Parse JSON dari AI. Gagal → kembalikan hold dict, tidak raise."""
    try:
        return _parse_json(text)
    except Exception:
        return {"action": "hold", "confidence": 0, "reason": "parse error"}


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