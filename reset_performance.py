"""Reset performance state. Posisi terbuka di-exchange TIDAK disentuh.

Script ini reset:
- state.json (consecutive_losses, cooldown)
- bot_state.json.balance (daily-start baseline)
- candidate_memory.json (cooldown per-symbol)

TIDAK reset:
- trades.json (history)
- riwayat_transaksi/ (history)
- bot.log
- Posisi terbuka di-exchange (COTIUSDT atau lainnya)

Jalankan saat:
1. Tidak ada posisi terbuka di-exchange, ATAU
2. Anda siap restart bot setelah script ini

Usage:
python reset_performance.py
"""
import json
import os
import time
import urllib.request

BASE = os.path.dirname(os.path.abspath(__file__))


def load_json(name, default):
 p = os.path.join(BASE, name)
 if not os.path.exists(p):
 return default
 with open(p) as f:
 return json.load(f)


def save_json(name, data):
 p = os.path.join(BASE, name)
 tmp = p + ".tmp"
 with open(tmp, "w") as f:
 json.dump(data, f, indent=2)
 os.replace(tmp, p)


def get_bybit_wallet_balance():
 """Ambil current wallet balance dari Bybit (placeholder — baca dari .env atau daemon)."""
 bot_state = load_json("bot_state.json", {})
 return bot_state.get("balance", None)


def main():
 print("=" * 60)
 print("RESET PERFORMANCE STATE")
 print("=" * 60)

 confirm = input("\nPosisi COTIUSDT (atau posisi lain) masih terbuka? (y/n): ").strip().lower()
 if confirm == "y":
 print("\nPERINGATAN: Reset dengan posisi terbuka akan me-reset risk state.")
 print("Bot mungkin akan trigger duplicate-entry protection atau gagal adopt posisi.")
 print("REKOMENDASI: close posisi dulu via dashboard, baru reset.")
 confirm2 = input("Lanjut tetap? (yes/no): ").strip().lower()
 if confirm2 != "yes":
 print("Batal.")
 return

 # 1. Reset state.json
 print("\n[1/3] Reset state.json...")
 state = {
 "consecutive_losses": 0,
 "daily_start_balance": None,
 "daily_start_day": None,
 "cooldown_until": 0,
 }
 save_json("state.json", state)
 print(" -> consecutive_losses=0, cooldown_until=0")

 # 2. Reset bot_state.json (hapus cooldown fields, preserve position)
 print("\n[2/3] Reset bot_state.json cooldown fields...")
 bot_state = load_json("bot_state.json", {})
 bot_state["consecutive_losses"] = 0
 bot_state["cooldown_until"] = 0
 bot_state["daily_loss_pct"] = 0.0
 bot_state["daily_pnl_pct"] = 0.0
 # balance: jangan reset (current wallet balance). daily_start_balance di-handle oleh risk_manager.on_new_day()
 save_json("bot_state.json", bot_state)
 print(" -> consecutive_losses=0, cooldown_until=0, balance preserved")

 # 3. Reset candidate_memory.json (hapus semua cooldown, mulai fresh)
 print("\n[3/3] Reset candidate_memory.json...")
 save_json("candidate_memory.json", {})
 print(" -> cleared all symbol cooldowns")

 print("\n" + "=" * 60)
 print("DONE. State performance sudah di-reset.")
 print("Sekarang RESTART BOT agar perubahan生效.")
 print("Posisi terbuka di-exchange (kalau ada) akan di-adopt saat restart.")
 print("=" * 60)


if __name__ == "__main__":
 main()