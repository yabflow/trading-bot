import json
import os
import time
import urllib.request

from dotenv import load_dotenv

load_dotenv(override=False)

AI_RETRY = 1                       # 1x retry cukup; model lambat → langsung cadangan
AI_RECOVERY_SECONDS = 900          # cek balik ke model utama tiap 15 menit
AI_TIMEOUT = 20                    # detik; cepat gagal kalau model tidak merespon


def _env_path():
    return os.path.join(os.path.dirname(__file__), ".env")


def _load_env():
    data = {}
    try:
        with open(_env_path()) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                data[k.strip()] = v.strip()
    except Exception:
        pass
    return data


def reload_config():
    """Reload konfigurasi AI dari .env tanpa perlu restart bot.

    Dipanggil di analyze() agar perubahan model dari dashboard langsung
    aktif di proses yang sedang jalan.
    """
    global AI_BASE_URL, AI_API_KEY, AI_MODELS, AI_MODEL, _model_idx, _last_recovery_check
    env = _load_env()
    AI_BASE_URL = env.get("AI_BASE_URL", "").strip()
    AI_API_KEY = env.get("AI_API_KEY", "").strip()
    _models_raw = (env.get("AI_MODELS") or env.get("AI_MODEL") or "").strip()
    new_models = [m.strip() for m in _models_raw.split(",") if m.strip()]
    if new_models:
        AI_MODELS = new_models
        AI_MODEL = AI_MODELS[0]
        _model_idx = 0
        _last_recovery_check = time.time()
    _require_config(AI_BASE_URL, AI_API_KEY, AI_MODELS)


AI_BASE_URL = (os.getenv("AI_BASE_URL") or "").strip()
AI_API_KEY = (os.getenv("AI_API_KEY") or "").strip()


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
_active_model = AI_MODELS[0] if AI_MODELS else None  # model terakhir yang berhasil dipakai (untuk indikator dashboard)


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

PROMPT = """Kamu trader crypto profesional di Bybit USDT Perpetual Futures dengan win rate tinggi. Analisis SATU kandidat coin dan putuskan entry atau tidak.

Data kandidat (lengkap):
{market_data}

Posisi saat ini: {position}

=== PRIORITAS #1: JANGAN RUGI (defense first) ===
Tugas utamamu BUKAN mencari trade, tapi MENGHINDARI trade jelek. Lebih baik hold daripada rugi. Hanya entry kalau peluang menang jelas-jelas lebih besar dari risiko.

=== ANALISIS WAJIB (urutan) ===

1. MARKET REGIME (field market_regime) — gerbang pertama:
   - bear/panic: JANGAN LONG melawan arus. Cari SHORT yang searah, atau hold.
   - bull: JANGAN SHORT melawan arus. Cari LONG yang searah, atau hold.
   - sideways: hanya entry kalau setup coin sangat bersih dan R/R >= 2.
   - high_vol: SL wajib lebih lebar dari ATR, atau hold.

2. RSI (overbought/oversold) — sinyal arah:
   - RSI 1h/4h > 75 (apalagi > 85) = overbought ekstrem → prioritas SHORT (mean reversion), BUKAN long.
   - RSI 1h/4h < 25 = oversold ekstrem → prioritas LONG (reversal), BUKAN short.
   - RSI 40-65 = momentum sehat, ikuti tren.
   - Koin micin (kapitalisasi kecil) overbought = kandidat SHORT terbaik (sering koreksi tajam).

3. KESELARASAN ARAH — semua harus sepakat:
   - TF dominan (1h/4h) HARUS searah dengan arah entry. Kalau 1h/4h berlawanan, hold.
   - 5m/15m boleh sedikit konflik, tapi jangan entry melawan 1h/4h.

4. FUTURES DATA (field futures):
   - funding_rate sangat positif = long crowded → risiko koreksi turun (dukung SHORT).
   - funding_rate sangat negatif = short crowded → risiko squeeze (jangan SHORT).
   - long_short_ratio > 0.75 = mayoritas long (crowded) → reversal turun lebih mungkin.
   - open_interest turun = posisi ditutup (trend melemah), jangan kejar.

5. SUPPORT/RESISTANCE: entry harus dekat level yang jelas. SL di luar S/R (bukan di tengah noise). TP di level lawan yang realistis.

6. VOLUME: entry butuh volume konfirmasi. Volume kering + break = fake.

7. RISK/REWARD: wajib >= 1.5, idealnya >= 2. Kalau < 1.5 → hold, TIDAK ada pengecualian.

=== ATURAN ENTRY (tanpa pengecualian) ===
- action "long" hanya kalau: regime bull/sideways, 1h/4h bullish, RSI tidak overbought ekstrem, R/R >= 1.5.
- action "short" hanya kalau: regime bear/sideways ATAU RSI overbought ekstrem, 1h/4h bearish (atau overbought untuk short mean-reversion), R/R >= 1.5.
- Kalau ragu atau ada konflik TF keras → "hold".
- Jangan entry hanya karena 1 faktor bagus; butuh konvergensi minimal 3 faktor searah.

=== CONFIDENCE (0-100) — jujur & konsisten ===
- 85-100: setup sempurna — regime searah, multi-TF searah, RSI ekstrem konfirmasi, volume konfirmasi, R/R >= 2.
- 70-84: setup kuat — mayoritas faktor searah, R/R >= 1.8.
- 60-69: setup layak — cukup faktor searah, R/R >= 1.5, sedikit konflik minor.
- 40-59: marginal — ada konflik, sebaiknya hold.
- 0-39: lemah / melawan regime → WAJIB hold.

=== KEPUTUSAN ===
- Entry (long/short) hanya kalau confidence >= 60 DAN R/R >= 1.5 DAN searah regime.
- Selain itu "hold".
- Kualitas > kuantitas. Satu trade bagus lebih baik dari sepuluh trade abu-abu.

Balas HANYA JSON (tanpa teks lain):
{{"action":"long"|"short"|"hold","confidence":0-100,"entry":angka,"stop_loss":angka,"take_profit":angka,"setup_type":"breakout|momentum|pullback|trend_continuation|volume_spike|reversal|other","reason":"satu kalimat padat"}}"""


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
    reload_config()
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
        "stream": False,
    }).encode()
    req = urllib.request.Request(
        AI_BASE_URL.rstrip("/") + "/chat/completions",
        data=body,
        headers={
            "Authorization": "Bearer " + AI_API_KEY,
            "Content-Type": "application/json",
            "User-Agent": "TradingBot/1.0",
        },
    )
    with urllib.request.urlopen(req, timeout=AI_TIMEOUT) as r:
        raw = r.read().decode("utf-8", errors="replace")

    # Proxy atau gateway bisa return standard JSON atau SSE (text/event-stream)
    content = None
    if "data:" in raw:
        parts = []
        for chunk in raw.split("data:"):
            chunk = chunk.strip()
            if not chunk or chunk == "[DONE]":
                continue
            try:
                obj = json.loads(chunk)
                choice = obj.get("choices", [{}])[0]
                txt = choice.get("delta", {}).get("content") or choice.get("message", {}).get("content") or ""
                if txt:
                    parts.append(txt)
            except Exception:
                continue
        if parts:
            content = "".join(parts)

    if content is None:
        try:
            resp = json.loads(raw)
            choice = resp.get("choices", [{}])[0]
            content = choice.get("message", {}).get("content") or choice.get("delta", {}).get("content")
        except Exception:
            pass

    if not content:
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