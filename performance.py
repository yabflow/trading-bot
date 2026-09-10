"""Hitung statistik kinerja bot dari trade history.

Win rate, total trade, profit/loss, per periode (hari/7hari/bulan/tahun).
Read-only, tidak menyentuh order.
"""
import os
import time

BASE_DIR = os.path.dirname(__file__)
HISTORY_DIR = os.path.join(BASE_DIR, "riwayat_transaksi")


def _load_all_trades():
    trades = []
    if not os.path.exists(HISTORY_DIR):
        return trades
    for f in sorted(os.listdir(HISTORY_DIR)):
        if not f.endswith(".json"):
            continue
        try:
            import json
            with open(os.path.join(HISTORY_DIR, f)) as fp:
                data = json.load(fp)
            for t in data.get("trades", []):
                t.setdefault("date", data.get("date", ""))
                trades.append(t)
        except Exception:
            pass
    return trades


def _period_key(date_str, period):
    if period == "day":
        return date_str
    if period == "7d":
        try:
            import datetime
            d = datetime.datetime.strptime(date_str, "%Y-%m-%d")
            return (d - datetime.timedelta(days=d.weekday())).strftime("%Y-%m-%d")
        except Exception:
            return date_str
    if period == "month":
        return date_str[:7]
    if period == "year":
        return date_str[:4]
    return date_str


def compute(period="all"):
    """Kembalikan statistik kinerja.

    period: "all", "day", "7d", "month", "year"
    """
    trades = _load_all_trades()
    sells = [t for t in trades if "CLOSE" in t.get("action", "")]
    buys = [t for t in trades if "LONG" in t.get("action", "") or "SHORT" in t.get("action", "")]

    wins = [t for t in sells if float(t.get("pnl", 0)) > 0]
    losses = [t for t in sells if float(t.get("pnl", 0)) <= 0]

    total_pnl = sum(float(t.get("pnl", 0)) for t in sells)
    total_pnl_usdt = total_pnl  # pnl sudah dalam %; USDT dihitung dari summary

    # profit factor
    gross_win = sum(float(t.get("pnl", 0)) for t in wins)
    gross_loss = abs(sum(float(t.get("pnl", 0)) for t in losses))
    profit_factor = (gross_win / gross_loss) if gross_loss > 0 else (gross_win if gross_win > 0 else 0)

    # avg win / avg loss
    avg_win = (gross_win / len(wins)) if wins else 0
    avg_loss = (gross_loss / len(losses)) if losses else 0

    # win rate
    win_rate = (len(wins) / len(sells) * 100) if sells else 0

    return {
        "period": period,
        "total_sells": len(sells),
        "total_buys": len(buys),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(win_rate, 2),
        "total_pnl_pct": round(total_pnl, 2),
        "profit_factor": round(profit_factor, 2),
        "avg_win_pct": round(avg_win, 2),
        "avg_loss_pct": round(avg_loss, 2),
        "best_trade": round(max((float(t.get("pnl", 0)) for t in sells), default=0), 2),
        "worst_trade": round(min((float(t.get("pnl", 0)) for t in sells), default=0), 2),
    }


def _history_summaries():
    """Kembalikan list summary harian dari folder riwayat."""
    summaries = []
    if not os.path.exists(HISTORY_DIR):
        return summaries
    import json
    for f in sorted(os.listdir(HISTORY_DIR)):
        if not f.endswith(".json"):
            continue
        try:
            with open(os.path.join(HISTORY_DIR, f)) as fp:
                summaries.append(json.load(fp))
        except Exception:
            pass
    return summaries


def group_by_period(period):
    """Group summary harian ke dalam periode (day/7d/month/year).

    Kembalikan list dict: {key, trades, pnl_pct, pnl_usdt, win_rate}.
    """
    summaries = _history_summaries()
    groups = {}
    for s in summaries:
        key = _period_key(s.get("date", ""), period)
        if key not in groups:
            groups[key] = {"trades": 0, "pnl_pct": 0.0, "pnl_usdt": 0.0, "wins": 0, "total": 0}
        g = groups[key]
        g["trades"] += s.get("total_trades", 0)
        g["pnl_pct"] += s.get("daily_pnl_pct", 0) * 100
        g["pnl_usdt"] += s.get("daily_pnl_usdt", 0)
        for t in s.get("trades", []):
            if "CLOSE" in t.get("action", ""):
                g["total"] += 1
                if float(t.get("pnl", 0)) > 0:
                    g["wins"] += 1

    result = []
    for key in sorted(groups.keys()):
        g = groups[key]
        win_rate = (g["wins"] / g["total"] * 100) if g["total"] else 0
        result.append({
            "key": key,
            "trades": g["trades"],
            "pnl_pct": round(g["pnl_pct"], 2),
            "pnl_usdt": round(g["pnl_usdt"], 2),
            "win_rate": round(win_rate, 2),
            "wins": g["wins"],
            "total_sells": g["total"],
        })
    return result


def export_performance(period="all"):
    """Export statistik kinerja ke folder riwayat_transaksi."""
    os.makedirs(HISTORY_DIR, exist_ok=True)
    stats = compute(period)
    groups = group_by_period(period) if period != "all" else []
    lines = _format_performance(stats, period, groups)
    fname = f"kinerja_{period}.txt"
    f = os.path.join(HISTORY_DIR, fname)
    with open(f, "w") as fp:
        fp.write(lines)
    return f


def _format_performance(stats, period, groups):
    lines = []
    lines.append("=" * 50)
    lines.append(f"LAPORAN KINERJA BOT - {period.upper()}")
    lines.append(f"Dibuat: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append("=" * 50)
    lines.append("")
    lines.append(f"Total BUY        : {stats['total_buys']}")
    lines.append(f"Total SELL       : {stats['total_sells']}")
    lines.append(f"Win              : {stats['wins']}")
    lines.append(f"Loss             : {stats['losses']}")
    lines.append(f"Win Rate         : {stats['win_rate']}%")
    lines.append(f"Total P&L        : {stats['total_pnl_pct']:+.2f}%")
    lines.append(f"Profit Factor    : {stats['profit_factor']}")
    lines.append(f"Avg Win          : {stats['avg_win_pct']:+.2f}%")
    lines.append(f"Avg Loss         : {stats['avg_loss_pct']:+.2f}%")
    lines.append(f"Best Trade       : {stats['best_trade']:+.2f}%")
    lines.append(f"Worst Trade      : {stats['worst_trade']:+.2f}%")
    lines.append("")
    if groups:
        lines.append("RINCIAN PER PERIODE:")
        lines.append("-" * 50)
        for g in groups:
            lines.append(f"{g['key']}  |  {g['trades']} trade  |  P&L {g['pnl_pct']:+.2f}% ({g['pnl_usdt']:+.2f} USDT)  |  WR {g['win_rate']}%")
    lines.append("")
    lines.append("=" * 50)
    return "\n".join(lines)


if __name__ == "__main__":
    import json
    print(json.dumps(compute("all"), indent=2, ensure_ascii=False))
