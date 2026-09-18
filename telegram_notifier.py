"""Telegram notifier untuk trade entry/exit.

Demo mode: kalau TELEGRAM_BOT_TOKEN belum diset, notifikasi hanya di-log
dan disimpan ke telegram_outbox.json (dry-run). Tidak kirim HTTP request.

Aktivasi:
 1. Chat @BotFather di Telegram -> /newbot -> dapat token
 2. Chat @userinfobot -> dapat chat_id
 3. Set di .env:
 TELEGRAM_BOT_TOKEN=123:abc
 TELEGRAM_CHAT_ID=123456789
 4. Restart bot
"""
import json
import os
import time
import urllib.request
import urllib.parse

BASE = os.path.dirname(__file__)
OUTBOX = os.path.join(BASE, "telegram_outbox.json")

_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()
_ENABLED = bool(_TOKEN and _CHAT_ID)


def _append_outbox(record):
    """Simpan record ke outbox file (untuk demo/test)."""
    try:
        data = []
        if os.path.exists(OUTBOX):
            with open(OUTBOX) as f:
                data = json.load(f)
        data.append(record)
        data = data[-200:]
        tmp = OUTBOX + ".tmp"
        with open(tmp, "w") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, OUTBOX)
    except Exception as e:
        print(f"[TELEGRAM] outbox write error: {e}")


def _send_http(text):
    """Kirim HTTP POST ke Telegram Bot API."""
    try:
        url = f"https://api.telegram.org/bot{_TOKEN}/sendMessage"
        payload = urllib.parse.urlencode({
            "chat_id": _CHAT_ID,
            "text": text,
            "parse_mode": "HTML",
        }).encode()
        req = urllib.request.Request(url, data=payload, method="POST")
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status == 200
    except Exception as e:
        print(f"[TELEGRAM] HTTP error: {e}")
        return False


def notify_entry(trade):
    """trade dict minimal: {action, qty, price, confidence, setup_type, reason, regime}."""
    symbol = (trade.get("action") or "").replace("LONG ", "").replace("SHORT ", "").strip()
    side_emoji = "LONG" if "LONG" in trade.get("action", "") else ("SHORT" if "SHORT" in trade.get("action", "") else "?")
    emoji = "\u2191" if side_emoji == "LONG" else "\u2193"
    text = (
        f"{emoji} <b>ENTRY {side_emoji} {symbol}</b>\n"
        f"Price: <code>{trade.get("price", "?")}</code>\n"
        f"Qty: {trade.get("qty", "?")}\n"
        f"Conf: {trade.get("confidence", "?")} | Setup: {trade.get("setup_type", "?")}\n"
        f"Regime: {trade.get("regime", "?")} | BTC: {trade.get("btc_trend", "?")}\n"
        f"Reason: {trade.get("reason", "")[:200]}"
)
    record = {
        "t": time.strftime("%Y-%m-%d %H:%M:%S"),
        "type": "entry",
        "trade": trade,
        "sent": False,
    }
    if _ENABLED:
        record["sent"] = _send_http(text)
    _append_outbox(record)
    return record


def notify_exit(trade, pnl_pct):
    """trade dict punya {action (CLOSE ...), price, qty}, pnl_pct adalah float."""
    symbol = (trade.get("action") or "").replace("CLOSE ", "").split(" ")[0]
    emoji = "\u2705" if pnl_pct >= 0 else "\u274c"
    text = (
        f"{emoji} <b>EXIT {symbol}</b>\n"
        f"Exit: <code>{trade.get("price", "?")}</code>\n"
        f"P&L: <code>{pnl_pct:+.2%}</code>\n"
        f"Reason: {trade.get("reason", "")[:200]}"
)
    record = {
        "t": time.strftime("%Y-%m-%d %H:%M:%S"),
        "type": "exit",
        "trade": trade,
        "pnl_pct": pnl_pct,
        "sent": False,
    }
    if _ENABLED:
        record["sent"] = _send_http(text)
    _append_outbox(record)
    return record


def status():
    """Return status untuk debugging."""
    return {
        "enabled": _ENABLED,
        "token_set": bool(_TOKEN),
        "chat_id_set": bool(_CHAT_ID),
        "outbox_path": OUTBOX,
    }

