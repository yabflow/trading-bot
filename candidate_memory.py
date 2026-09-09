"""Candidate memory: track kandidat agar AI tidak spam + peluang berkembang tetap dipantau.

- Kandidat yang AI bilang HOLD tidak dilupakan, tetap dilacak.
- Kandidat hanya dikirim ulang ke AI jika ada perubahan signifikan.
- Cooldown mencegah spam AI untuk coin yang sama.
"""
import json
import os
import time

BASE_DIR = os.path.dirname(__file__)
MEMORY_FILE = os.path.join(BASE_DIR, "candidate_memory.json")

# cooldown: coin yang sudah dianalisis AI tidak dianalisis ulang dalam X detik
AI_COOLDOWN_SECONDS = int(os.getenv("SCAN_AI_COOLDOWN", "900"))  # 15 menit
# perubahan skor signifikan = kirim ulang walau masih cooldown
SCORE_CHANGE_TRIGGER = float(os.getenv("SCAN_SCORE_TRIGGER", "5.0"))


class CandidateMemory:
    def __init__(self):
        self.data = {}
        self._load()

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

    def should_analyze(self, symbol, score):
        """Apakah kandidat layak dikirim ke AI sekarang?"""
        now = time.time()
        rec = self.data.get(symbol)
        if rec is None:
            # kandidat baru → analisis
            return True, "baru"
        last_score = rec.get("score", 0)
        last_time = rec.get("last_ai_time", 0)
        # perubahan signifikan → analisis ulang walau cooldown
        if score - last_score >= SCORE_CHANGE_TRIGGER:
            return True, f"score-naik-{score-last_score:.1f}"
        # cooldown → skip
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
