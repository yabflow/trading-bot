import json
import os
import time
import urllib.request

from dotenv import load_dotenv

load_dotenv(override=False)

AI_RETRY = 3                       # retry tiap model sebelum pindah cadangan
AI_RECOVERY_SECONDS = 3600         # cek balik ke model utama tiap 1 jam


def _require_config(base_url, api_key, models):
    """Fail-safe: konfigurasi AI wajib lengkap, tidak ada fallback ke provider lain."""
    if not (base_url and api_key and models):
        raise RuntimeError(
            "Konfigurasi AI tidak lengkap: AI_BASE_URL, AI_API_KEY, dan AI_MODEL(S) wajib diset di .env. "
            "Tidak ada fallback ke provider lain."
        )


AI_BASE_URL = (os.getenv("AI_BASE_URL") or "").strip()
AI_API_KEY = (os.getenv("AI_API_KEY") or "").strip()

# Urutan model prioritas: AI_MODELS="model1,model2,model3" (1 utama, 3 terakhir).
# AI_MODEL (satu) tetap didukung sebagai model utama.
_models_raw = (os.getenv("AI_MODELS") or os.getenv("AI_MODEL") or "").strip()
AI_MODELS = [m.strip() for m in _models_raw.split(",") if m.strip()]

_require_config(AI_BASE_URL, AI_API_KEY, AI_MODELS)

AI_MODEL = AI_MODELS[0]  # backward-compat: model aktif saat ini

# round-robin state: index model aktif + kapan terakhir cek pemulihan
_model_idx = 0
_last_recovery_check = time.time()
_active_model = None  # model terakhir yang berhasil dipakai (untuk indikator dashboard)


def _current_model():
    """Model aktif sekarang (bisa bergeser saat fallback/recovery)."""
    global _model_idx, _last_recovery_check
    # auto-recovery: tiap AI_RECOVERY_SECONDS coba balik ke model prioritas (idx 0)
    if _model_idx > 0 and (time.time() - _last_recovery_check) >= AI_RECOVERY_SECONDS:
        _last_recovery_check = time.time()
        _model_idx = 0
    return AI_MODELS[_model_idx]


def _on_model_failed():
    """Model aktif gagal semua retry → geser ke cadangan berikutnya."""
    global _model_idx, _last_recovery_check
    _last_recovery_check = time.time()
    if _model_idx < len(AI_MODELS) - 1:
        _model_idx += 1
    return AI_MODELS[_model_idx]

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
    # arah tidak valid → hold (fail-safe). TP tidak wajib (trailing murni), SL wajib.
    if action == "long":
        if entry <= 0 or not (stop_loss < entry):
            out["action"] = "hold"
    elif action == "short":
        if entry <= 0 or not (stop_loss > entry):
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
    global _active_model
    prompt = PROMPT.format(market_data=json.dumps(market_data, ensure_ascii=False), position=position)
    last_err = None
    # coba setiap model (mulai dari model aktif), tiap model retry AI_RETRY kali
    for attempt in range(len(AI_MODELS)):
        model = _current_model()
        for _ in range(AI_RETRY):
            try:
                res = _analyze_with_model(model, prompt)
                _active_model = model
                return res
            except Exception as e:
                last_err = e
        # semua retry model ini gagal → geser cadangan
        _on_model_failed()
    raise RuntimeError(f"Semua model AI gagal: {last_err}")


def _analyze_with_model(model, prompt):
    body = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.3,
        "max_tokens": 2000,
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
    with urllib.request.urlopen(req, timeout=60) as r:
        raw = r.read().decode("utf-8", errors="replace")

    # proxy/9router kadang return SSE (text/event-stream) walau stream=False:
    #   {json}\ndata: [DONE]\n  → ambil bagian JSON pertama saja.
    content = None
    for chunk in raw.split("data:"):
        chunk = chunk.strip()
        if not chunk or chunk == "[DONE]":
            continue
        try:
            obj = json.loads(chunk)
        except Exception:
            continue
        msg = obj.get("choices", [{}])[0].get("message", {}).get("content")
        if msg:
            content = msg
            break
    if content is None:
        try:
            resp = json.loads(raw)
            content = resp["choices"][0]["message"]["content"]
        except Exception:
            raise ValueError("AI response kosong/streaming tak terduga")
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