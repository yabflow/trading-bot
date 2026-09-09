import json
import os
import threading
import time

BASE_DIR = os.path.dirname(__file__)
TRADES_FILE = os.path.join(BASE_DIR, "trades.json")
HISTORY_DIR = os.path.join(BASE_DIR, "riwayat_transaksi")
BOT_STATE_FILE = os.path.join(BASE_DIR, "bot_state.json")


class BotState:
    def __init__(self):
        self.lock = threading.Lock()
        self.data = {
            "running": False,
            "dry_run": False,
            "started_at": None,
            "balance": 0.0,
            "daily_start_balance": 0.0,
            "daily_pnl_pct": 0.0,
            "daily_pnl_usdt": 0.0,
            "risk_pct": 0.0,
            "consecutive_losses": 0,
            "cooldown": False,
            "position": None,
            "highest_price": None,
            "trailing_pct": 0.0,
            "last_ai_action": None,
            "last_ai_confidence": 0,
            "last_ai_reason": "",
            "last_ai_time": None,
            "last_update": None,
            "alert": None,
            "scanned_pairs": 0,
            "candidates": [],
            "ai_results": [],
            "trades": [],
            "history": [],
            "log": [],
        }
        self._load_trades()
        self._load_history()
        self._load_bot_state()

    def _load_trades(self):
        if os.path.exists(TRADES_FILE):
            try:
                with open(TRADES_FILE) as fp:
                    self.data["trades"] = json.load(fp)
            except Exception:
                pass

    def _save_trades(self):
        with open(TRADES_FILE, "w") as fp:
            json.dump(self.data["trades"], fp)

    def _load_bot_state(self):
        if os.path.exists(BOT_STATE_FILE):
            try:
                with open(BOT_STATE_FILE) as fp:
                    s = json.load(fp)
                for k in ("running", "dry_run", "started_at", "balance",
                          "daily_start_balance", "daily_pnl_pct", "daily_pnl_usdt",
                          "risk_pct", "consecutive_losses", "cooldown", "position",
                          "highest_price", "trailing_pct", "last_ai_action",
                          "last_ai_confidence", "last_ai_reason", "last_ai_time",
                          "last_update", "alert", "scanned_pairs", "candidates", "ai_results", "log"):
                    if k in s:
                        self.data[k] = s[k]
            except Exception:
                pass

    def persist(self):
        with self.lock:
            keys = ("running", "dry_run", "started_at", "balance",
                    "daily_start_balance", "daily_pnl_pct", "daily_pnl_usdt",
                    "risk_pct", "consecutive_losses", "cooldown", "position",
                    "highest_price", "trailing_pct", "last_ai_action",
                    "last_ai_confidence", "last_ai_reason", "last_ai_time",
                    "last_update", "alert", "scanned_pairs", "candidates", "ai_results", "log")
            snapshot = {k: self.data[k] for k in keys}
        tmp = BOT_STATE_FILE + ".tmp"
        with open(tmp, "w") as fp:
            json.dump(snapshot, fp, ensure_ascii=False)
        os.replace(tmp, BOT_STATE_FILE)

    def _history_file(self, date_str):
        os.makedirs(HISTORY_DIR, exist_ok=True)
        return os.path.join(HISTORY_DIR, f"riwayat_{date_str}.json")

    def _load_history(self):
        if not os.path.exists(HISTORY_DIR):
            return
        for f in sorted(os.listdir(HISTORY_DIR)):
            if f.endswith(".json"):
                try:
                    with open(os.path.join(HISTORY_DIR, f)) as fp:
                        self.data["history"].append(json.load(fp))
                except Exception:
                    pass

    def update(self, **kw):
        with self.lock:
            self.data.update(kw)
            self.data["last_update"] = time.strftime("%H:%M:%S")
        self.persist()

    def log(self, msg):
        with self.lock:
            self.data["log"].append({"t": time.strftime("%H:%M:%S"), "m": msg})
            self.data["log"] = self.data["log"][-200:]

    def set_alert(self, msg):
        with self.lock:
            self.data["alert"] = msg
        self.persist()

    def clear_alert(self):
        with self.lock:
            self.data["alert"] = None
        self.persist()

    def add_trade(self, trade):
        with self.lock:
            trade["date"] = time.strftime("%Y-%m-%d")
            self.data["trades"].append(trade)
            self.data["trades"] = self.data["trades"][-500:]
            self._save_trades()

    def save_daily_summary(self, summary):
        date_str = summary.get("date", time.strftime("%Y-%m-%d"))
        with self.lock:
            f = self._history_file(date_str)
            with open(f, "w") as fp:
                json.dump(summary, fp, ensure_ascii=False, indent=2)
            for i, h in enumerate(self.data["history"]):
                if h.get("date") == date_str:
                    self.data["history"][i] = summary
                    return
            self.data["history"].append(summary)

    def export_txt(self, date_str=None):
        if date_str is None:
            date_str = time.strftime("%Y-%m-%d")
        summary = None
        for h in self.data["history"]:
            if h.get("date") == date_str:
                summary = h
                break
        if summary is None:
            summary = self._build_summary(date_str)
        lines = self._format_txt(summary)
        f = os.path.join(HISTORY_DIR, f"riwayat_{date_str}.txt")
        os.makedirs(HISTORY_DIR, exist_ok=True)
        with open(f, "w") as fp:
            fp.write(lines)
        return f

    def _build_summary(self, date_str):
        trades = [t for t in self.data["trades"] if t.get("date") == date_str]
        return {
            "date": date_str,
            "trades": trades,
            "total_trades": len(trades),
            "balance": self.data.get("balance", 0),
            "daily_pnl_pct": self.data.get("daily_pnl_pct", 0),
            "daily_pnl_usdt": self.data.get("daily_pnl_usdt", 0),
        }

    def _format_txt(self, summary):
        d = summary.get("date", "")
        lines = []
        lines.append("=" * 40)
        lines.append(f"RIWAYAT TRADING BOT - {d}")
        lines.append("=" * 40)
        lines.append("")
        lines.append(f"Saldo akhir        : {summary.get('balance', 0):.2f} USDT")
        lines.append(f"P&L hari ini       : {summary.get('daily_pnl_pct', 0)*100:+.2f}% ({summary.get('daily_pnl_usdt', 0):+.2f} USDT)")
        lines.append(f"Total trade        : {summary.get('total_trades', 0)}")
        lines.append("")
        trades = summary.get("trades", [])
        if trades:
            lines.append("DETAIL TRADE:")
            lines.append("-" * 40)
            for t in trades:
                lines.append(f"{t.get('date','')} {t.get('t','')} | {t.get('action','')} | qty {t.get('qty','')} | harga {t.get('price','')} | P&L {t.get('pnl','')}")
        else:
            lines.append("Tidak ada trade hari ini.")
        lines.append("")
        lines.append("=" * 40)
        lines.append(f"Dibuat: {time.strftime('%Y-%m-%d %H:%M:%S')}")
        return "\n".join(lines)

    def snapshot(self):
        with self.lock:
            return json.dumps(self.data, ensure_ascii=False)


state = BotState()
