"""Candidate memory: track kandidat agar AI tidak spam + peluang berkembang tetap dipantau.

- Kandidat yang AI bilang HOLD tidak dilupakan, tetap dilacak.
- Kandidat hanya dikirim ulang ke AI jika ada perubahan signifikan.
- Cooldown mencegah spam AI untuk coin yang sama.
- WR per-symbol: track win/loss per symbol dari trades.json.
 Symbol dengan WR rendah akan ditahan di scanner.
"""
import json
import os
import time

BASE_DIR = os.path.dirname(__file__)
MEMORY_FILE = os.path.join(BASE_DIR, "candidate_memory.json")
TRADES_FILE = os.path.join(BASE_DIR, "trades.json")

# cooldown: coin yang sudah dianalisis AI tidak dianalisis ulang dalam X detik
AI_COOLDOWN_SECONDS = int(os.getenv("SCAN_AI_COOLDOWN", "900")) # 15 menit
# perubahan skor signifikan = kirim ulang walau masih cooldown
SCORE_CHANGE_TRIGGER = float(os.getenv("SCAN_SCORE_TRIGGER", "5.0"))
# WR threshold: symbol dengan WR < ini dari N>=WR_MIN_TRADES ditahan
WR_THRESHOLD = float(os.getenv("SYMBOL_WR_THRESHOLD", "0.30"))
WR_MIN_TRADES = int(os.getenv("SYMBOL_WR_MIN_TRADES", "4"))


def _load_symbol_stats():
    """Baca trades.json, hitung WR per-symbol. Return dict.
    Format: {symbol: {wins, losses, total, win_rate, last_pnl_pct}}
    """
    if not os.path.exists(TRADES_FILE):
        return {}
    try:
        with open(TRADES_FILE) as f:
            trades = json.load(f)
    except Exception:
        return {}
    stats = {}
    for t in trades:
        if t.get("pnl") == 0:
            continue # entry record, skip
        action = t.get("action", "")
        if not action.startswith("CLOSE"):
            continue # bukan close record
        # extract symbol from action: "CLOSE BTCUSDT (long)"
        parts = action.split()
        if len(parts) < 2:
            continue
        sym = parts[1]
        if sym not in stats:
            stats[sym] = {"wins": 0, "losses": 0, "total": 0, "win_rate": 0.0, "last_pnl": 0.0}
        pnl = float(t.get("pnl", 0))
        stats[sym]["total"] += 1
        if pnl > 0:
            stats[sym]["wins"] += 1
        else:
            stats[sym]["losses"] += 1
        stats[sym]["last_pnl"] = pnl
    for s, v in stats.items():
        if v["total"] > 0:
            v["win_rate"] = v["wins"] / v["total"]
    return stats


class CandidateMemory:
    def __init__(self):
        self.data = {}
        self._load()
        self._symbol_stats_cache = None
        self._symbol_stats_time = 0

    def _load(self):
        if os.path.exists(MEMORY_FILE):
            try:
                with open(MEMORY_FILE) as f:
                    self.data = json.load(f)
            except Exception:
                self.data = {}
    def _save(self):
        with open(MEMORY_FILE, "w") as f:
            json.dump(self.data, f)

    def _get_symbol_stats(self, force=False):
        """Cache symbol stats 60 detik."""
        now = time.time()
        if not force and self._symbol_stats_cache is not None and (now - self._symbol_stats_time) < 60:
            return self._symbol_stats_cache
        stats = _load_symbol_stats()
        self._symbol_stats_cache = stats
        self._symbol_stats_time = now
        return stats

    def is_symbol_blocked(self, symbol):
        """Return True kalau symbol punya WR rendah (configurable threshold).
        Tidak block kalau trades belum cukup.
        """
        stats = self._get_symbol_stats()
        s = stats.get(symbol)
        if s is None:
            return False, "no-data"
        if s["total"] < WR_MIN_TRADES:
            return False, 'insufficient-data(' + str(s['total']) + ')'
        if s["win_rate"] < WR_THRESHOLD:
            return True, 'low-wr(' + format(s['win_rate'], '.0%') + ' < ' + format(WR_THRESHOLD, '.0%') + ')'
        return False, "ok"

    def should_analyze(self, symbol, score):
        """Apakah kandidat layak dikirim ke AI sekarang?"""
        now = time.time()
        rec = self.data.get(symbol)
        if rec is None:
            return True, "baru"
        last_score = rec.get("score", 0)
        last_time = rec.get("last_ai_time", 0)
        if score - last_score >= SCORE_CHANGE_TRIGGER:
            return True, f"score-naik-{score-last_score:.1f}"
        if now - last_time < AI_COOLDOWN_SECONDS:
            return False, "cooldown"
        return True, "cooldown-selesai"

    def record_ai(self, symbol, score, action):
        self.data[symbol] = {
            "score": score,
            "last_ai_time": time.time(),
            "last_action": action,
        }
        self._save()

    def record_seen(self, symbol, score):
        """Catat kandidat terdeteksi (belum tentu dianalisis)."""
        if symbol not in self.data:
            self.data[symbol] = {"score": score, "last_ai_time": 0, "last_action": None}
            self._save()

    def snapshot(self):
        return dict(self.data)


memory = CandidateMemory()
