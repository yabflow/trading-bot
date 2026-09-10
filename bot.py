import os
import signal
import subprocess
import sys
import time
import threading

import bybit_client as bc
import ai_analyzer as ai
import candidate_memory
import guard
import position_manager as pm
import scanner
from risk_manager import RiskManager, LEVERAGE
from state import state

SHUTDOWN = False
SELL_REQUEST = False
DRY_RUN = os.getenv("DRY_RUN", "0") == "1"
INHIBITOR = None
INHIBIT_LOCK = threading.Lock()
INHIBIT_MONITOR = None


def _hold_suspend():
    """Jaga laptop tetap terjaga selama bot jalan.

    Coba berurutan (pertama yang berhasil dipakai):
    1. systemd-inhibit  (blocking, lock aktif selama proses hidup)
    2. DBus login1.Inhibit fd (fallback tanpa binary systemd-inhibit)

    Monitor thread di belakang memastikan lock tidak lepas diam-diam.
    """
    global INHIBITOR, INHIBIT_MONITOR

    def _arm():
        global INHIBITOR
        # Primary: systemd-inhibit
        try:
            INHIBITOR = subprocess.Popen(
                ["systemd-inhibit", "--what=sleep:idle:handle-lid-switch",
                 "--who=Trading-BOT", "--why=Bot aktif, jangan suspend", "sleep", "infinity"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return True
        except Exception:
            INHIBITOR = None

        # Fallback: DBus login1.Inhibit (fd tetap terbuka = lock aktif)
        try:
            import dbus
            bus = dbus.SystemBus()
            proxy = bus.get_object("org.freedesktop.login1", "/org/freedesktop/login1")
            iface = dbus.Interface(proxy, "org.freedesktop.login1.Manager")
            fd = iface.Inhibit("sleep", "Trading-BOT", "Bot aktif, jangan suspend", "delay")
            INHIBITOR = {"type": "dbus", "fd": fd}
            return True
        except Exception:
            INHIBITOR = None

        return False

    with INHIBIT_LOCK:
        _release_suspend_internal()
        if _arm():
            log("Suspend inhibition aktif")
        else:
            log("WARNING: inhibit suspend GAGAL (systemd-inhibit & DBus tidak tersedia)")

    if INHIBITOR is not None and INHIBIT_MONITOR is None:
        INHIBIT_MONITOR = threading.Thread(target=_monitor_inhibit, daemon=True)
        INHIBIT_MONITOR.start()


def _monitor_inhibit():
    """Re-arm inhibitor jika mati/lepas sebelum bot selesai."""
    global INHIBITOR
    while not SHUTDOWN:
        time.sleep(15)
        with INHIBIT_LOCK:
            if INHIBITOR is None:
                continue
            dead = False
            if isinstance(INHIBITOR, dict):
                if INHIBITOR["type"] == "dbus":
                    try:
                        os.fstat(INHIBITOR["fd"])
                    except OSError:
                        dead = True
            else:
                dead = INHIBITOR.poll() is not None
            if dead:
                log("Inhibit lepas, re-arm...")
                _release_suspend_internal()
                _arm_silent()
                if INHIBITOR is None:
                    break


def _arm_silent():
    global INHIBITOR
    try:
        INHIBITOR = subprocess.Popen(
            ["systemd-inhibit", "--what=sleep:idle:handle-lid-switch",
             "--who=Trading-BOT", "--why=Bot aktif, jangan suspend", "sleep", "infinity"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return
    except Exception:
        INHIBITOR = None
    try:
        import dbus
        bus = dbus.SystemBus()
        proxy = bus.get_object("org.freedesktop.login1", "/org/freedesktop/login1")
        iface = dbus.Interface(proxy, "org.freedesktop.login1.Manager")
        fd = iface.Inhibit("sleep", "Trading-BOT", "Bot aktif, jangan suspend", "delay")
        INHIBITOR = {"type": "dbus", "fd": fd}
    except Exception:
        INHIBITOR = None


def _release_suspend():
    global INHIBITOR, INHIBIT_MONITOR
    with INHIBIT_LOCK:
        _release_suspend_internal()
    INHIBIT_MONITOR = None


def _release_suspend_internal():
    global INHIBITOR
    if INHIBITOR is None:
        return
    try:
        if isinstance(INHIBITOR, dict):
            if INHIBITOR["type"] == "dbus":
                os.close(INHIBITOR["fd"])
        else:
            INHIBITOR.terminate()
            try:
                INHIBITOR.wait(timeout=2)
            except subprocess.TimeoutExpired:
                INHIBITOR.kill()
    except Exception:
        pass
    INHIBITOR = None
    log("Suspend inhibition dilepas")

CRASH_PCT = float(os.getenv("CRASH_PCT", "0.03"))
MAX_CONSECUTIVE_ERRORS = int(os.getenv("MAX_ERRORS", "10"))
SCAN_INTERVAL = int(os.getenv("SCAN_INTERVAL_SECONDS", "180"))
AI_INTERVAL = int(os.getenv("AI_INTERVAL_SECONDS", "900"))
AI_CONFIDENCE_MIN = int(os.getenv("AI_CONFIDENCE_MIN", "60"))

LIVE = not DRY_RUN


def on_terminate(sig, frame):
    global SHUTDOWN
    SHUTDOWN = True


def on_sell(sig, frame):
    global SELL_REQUEST
    SELL_REQUEST = True


signal.signal(signal.SIGTERM, on_terminate)
signal.signal(signal.SIGINT, on_terminate)
signal.signal(signal.SIGUSR1, on_sell)


def sleep_check(seconds):
    global SHUTDOWN, SELL_REQUEST
    for _ in range(int(seconds)):
        if SHUTDOWN or SELL_REQUEST:
            return
        time.sleep(1)


def log(msg):
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    state.log(msg)


def get_balance():
    return bc.get_wallet_balance()


def get_position_info():
    """Posisi aktif dari exchange. None jika tidak ada."""
    return pm.get_position()


def sell_all(rm):
    log("MENUTUP SEMUA POSISI...")
    if not DRY_RUN:
        try:
            r = pm.sell_all()
            log(f"Close all result: {r}")
            # #11: jika masih ada posisi setelah close, jangan anggap berhasil diam-diam.
            if any(str(x).startswith("STILL-OPEN") for x in r):
                state.set_alert(f"POSISI MASIH TERBUKA setelah sell_all: {r}. Retry diperlukan.")
                log("BOT: sell_all belum tuntas, posisi masih terbuka. Retry next loop.")
                return
        except Exception as e:
            log(f"Close all error: {e}")
            state.set_alert(f"GAGAL TUTUP POSISI: {e}")
    else:
        log("[DRY-RUN] close semua (skip)")
    rm.reset_trailing()
    state.update(position=None, highest_price=None, lowest_price=None)


def _save_daily(balance, rm):
    today = time.strftime("%Y-%m-%d")
    trades = [t for t in state.data.get("trades", []) if t.get("date") == today]
    state.save_daily_summary({
        "date": today,
        "trades": trades,
        "total_trades": len(trades),
        "balance": balance,
        "daily_pnl_pct": rm.daily_pnl_pct(balance),
        "daily_pnl_usdt": balance - (rm.daily_start_balance or balance),
        "risk_pct": rm.current_risk(),
    })


def _est_liq_price(action, entry):
    """Estimasi kasar harga likuidasi (isolated margin).
    dist = 1/leverage + maintenance. Jika dist >= 1 (leverage ~1x), likuidasi praktis tidak ada."""
    if entry <= 0:
        return None
    mcr = 0.005  # maintenance margin rate default Bybit linear
    dist = (1.0 / LEVERAGE) + mcr
    if dist >= 1.0:
        return None  # leverage terlalu rendah untuk ada risiko likuidasi
    if action == "long":
        return entry * (1 - dist)
    return entry * (1 + dist)


def _verify_sl_tp(symbol, action, expected_qty=None):
    """Verifikasi posisi + side + size + SL/TP exchange-side setelah entry.

    Return (ok, msg). ok=False berarti posisi aktif tanpa protection → emergency close.
    expected_qty: jika diberikan, qty posisi harus >= expected_qty (toleransi 5%).
    """
    detail = pm.get_position_detail(symbol)
    if detail is None:
        return False, "posisi tidak ditemukan setelah entry (mungkin tidak terisi)"
    if detail == "UNKNOWN":
        return False, "tidak bisa verifikasi posisi (API error)"

    # verify side
    expected_side = "Buy" if action == "long" else "Sell"
    if detail["side"] != expected_side:
        return False, f"side tidak cocok (ekspektasi {expected_side}, aktual {detail['side']})"

    # verify size/qty cocok dengan order yang dikirim
    if expected_qty is not None and expected_qty > 0:
        if detail["qty"] < expected_qty * 0.95:
            return False, f"qty posisi {detail['qty']} < ekspektasi {expected_qty} (partial fill?)"

    # verify SL & TP ada dan arah benar
    sl = detail.get("stop_loss")
    tp = detail.get("take_profit")
    if sl is None or tp is None:
        return False, f"SL/TP belum terpasang (SL={sl}, TP={tp})"
    if action == "long":
        if not (sl < detail["entry"] < tp):
            return False, f"SL/TP arah salah LONG (SL={sl} entry={detail['entry']} TP={tp})"
    else:
        if not (tp < detail["entry"] < sl):
            return False, f"SL/TP arah salah SHORT (TP={tp} entry={detail['entry']} SL={sl})"

    return True, "SL/TP terverifikasi"


def _emergency_close(symbol):
    """Tutup posisi darurat + verifikasi posisi benar-benar 0."""
    try:
        detail = pm.get_position_detail(symbol)
        if isinstance(detail, dict):
            bc.close_position(symbol, detail["side"], detail["qty"])
    except Exception as e:
        log(f"Emergency close error {symbol}: {e}")
    # verifikasi posisi = 0 setelah close
    time.sleep(1)
    after = pm.get_position_detail(symbol)
    if isinstance(after, dict):
        state.set_alert(f"EMERGENCY CLOSE GAGAL {symbol}: posisi masih {after['side']} qty={after['qty']}")
        log(f"BOT: EMERGENCY CLOSE GAGAL {symbol}, posisi masih terbuka.")
    elif after == "UNKNOWN":
        state.set_alert(f"EMERGENCY CLOSE {symbol}: status posisi UNKNOWN setelah close.")
        log(f"BOT: EMERGENCY CLOSE {symbol}, status UNKNOWN setelah close.")
    else:
        log(f"BOT: EMERGENCY CLOSE {symbol} sukses, posisi=0.")


def _reconcile_after_order_timeout(symbol, action, entry, qty, stop_loss, take_profit):
    """Setelah create_order timeout/error, cek state Bybit: apakah posisi terbentuk?

    Timeout bukan berarti order gagal. Jika posisi terbentuk:
      - dengan SL/TP valid → ambil alih (adopt) tanpa entry ulang.
      - tanpa SL/TP → pasang protection, jika gagal → emergency close.
    Jika tidak ada posisi → aman, tidak ada aksi.
    """
    time.sleep(1)
    detail = pm.get_position_detail(symbol)
    if detail == "UNKNOWN":
        state.set_alert(f"create_order timeout {symbol} + status posisi UNKNOWN. Perlu cek manual!")
        log(f"BOT: timeout {symbol} + status UNKNOWN. Manual review diperlukan.")
        return
    if not isinstance(detail, dict):
        log(f"BOT: timeout {symbol}, tidak ada posisi terbentuk. Aman.")
        return

    expected_side = "Buy" if action == "long" else "Sell"
    log(f"BOT: timeout {symbol} tapi posisi terbentuk ({detail['side']} qty={detail['qty']}). Adopt.")
    if detail["side"] != expected_side:
        log(f"BOT: side tidak cocok ({detail['side']} vs {expected_side}). Emergency close.")
        _emergency_close(symbol)
        return

    # cek SL/TP terpasang benar
    ok, msg = _verify_sl_tp(symbol, action)
    if ok:
        log(f"BOT: posisi {symbol} adopt dengan SL/TP verified.")
        return
    # coba pasang ulang SL/TP
    log(f"BOT: {symbol} tanpa protection ({msg}). Pasang SL/TP ulang.")
    try:
        bc.set_trading_stop(symbol, detail["side"], stop_loss=stop_loss, take_profit=take_profit)
        time.sleep(1)
        ok2, msg2 = _verify_sl_tp(symbol, action)
        if ok2:
            log(f"BOT: {symbol} SL/TP berhasil dipasang ulang.")
            return
        log(f"BOT: {symbol} SL/TP gagal dipasang ulang ({msg2}). Emergency close.")
    except Exception as e:
        log(f"BOT: set SL/TP ulang error {symbol}: {e}. Emergency close.")
    _emergency_close(symbol)


def _try_entry(rm, balance, symbol, price, signal_res):
    action = signal_res.get("action", "hold")
    if action not in ("long", "short"):
        log(f"BOT: Rejected {symbol} — action {action} tidak valid.")
        return

    entry = float(signal_res.get("entry", price))
    stop_loss = float(signal_res.get("stop_loss", 0))
    take_profit = float(signal_res.get("take_profit", 0))
    confidence = int(signal_res.get("confidence", 0))
    setup_type = signal_res.get("setup_type", "other")

    if stop_loss <= 0 or take_profit <= 0:
        log(f"BOT: Rejected {symbol} — AI tidak beri SL/TP valid.")
        return

    # validasi SL/TP sesuai arah
    if action == "long":
        if stop_loss >= entry or take_profit <= entry:
            log(f"BOT: Rejected {symbol} — SL/TP tidak valid untuk LONG (SL<entry<TP).")
            return
    else:  # short
        if stop_loss <= entry or take_profit >= entry:
            log(f"BOT: Rejected {symbol} — SL/TP tidak valid untuk SHORT (TP<entry<SL).")
            return

    # R/R minimum
    if not rm.validate_rr(action, entry, stop_loss, take_profit):
        log(f"BOT: Rejected {symbol} — R/R < MIN_RR.")
        return

    # setup sangat kuat: confidence tinggi + setup jelas → boleh risiko sampai 1%
    strong_setup = confidence >= 85 and setup_type in (
        "breakout", "momentum", "trend_continuation", "pullback", "volume_spike")

    qty = rm.position_size(balance, entry, stop_loss, confidence, strong_setup=strong_setup)
    if qty <= 0:
        log(f"BOT: Rejected {symbol} — position size 0.")
        return

    if not DRY_RUN:
        qty = bc.round_qty(qty, symbol=symbol)
        if qty <= 0:
            log(f"BOT: Rejected {symbol} — qty < minOrderQty.")
            return

        # cek jarak likuidasi: likuidasi harus jauh di luar SL
        liq = _est_liq_price(action, entry)
        if liq is not None:
            if action == "long" and liq >= stop_loss:
                log(f"BOT: Rejected {symbol} — likuidasi {liq:.4f} terlalu dekat SL {stop_loss}.")
                return
            if action == "short" and liq <= stop_loss:
                log(f"BOT: Rejected {symbol} — likuidasi {liq:.4f} terlalu dekat SL {stop_loss}.")
                return

    risk_pct = rm.current_risk(confidence=confidence, strong_setup=strong_setup)
    side_str = "Buy" if action == "long" else "Sell"
    if DRY_RUN:
        log(f"[DRY-RUN] {action.upper()} {symbol} {qty:.6f} @ {entry} | SL={stop_loss} TP={take_profit} | risk={risk_pct*100:.2f}% conf={confidence}")
    else:
        # #7 DUPLICATE-ORDER + #10 ONE-POSITION: cek posisi exchange SESUAT sebelum kirim order.
        # Jangan buka posisi kedua karena local state stale / signal berulang / race.
        cur = pm.get_position()
        if isinstance(cur, dict):
            log(f"BOT: Rejected {symbol} — sudah ada posisi aktif {cur['symbol']} {cur['side']}. Skip entry (one-position rule).")
            return
        if cur == "UNKNOWN":
            log(f"BOT: Rejected {symbol} — status posisi UNKNOWN (API error). Tidak entry untuk cegah duplicate.")
            state.set_alert(f"Entry dibatalkan {symbol}: status posisi UNKNOWN.")
            return

        try:
            bc.set_leverage(LEVERAGE, symbol=symbol)
        except Exception as e:
            log(f"Set leverage {symbol} warning: {e}")

        # #7: kirim order dengan timeout proteksi. Timeout BUKAN berarti order gagal —
        # bisa jadi sudah terisi. Verifikasi state Bybit sebelum lanjut.
        try:
            r = bc.create_order(side_str, qty, symbol=symbol,
                                stop_loss=stop_loss, take_profit=take_profit)
        except Exception as e:
            log(f"BOT: create_order timeout/error {symbol}: {e}. Cek state Bybit...")
            _reconcile_after_order_timeout(symbol, action, entry, qty, stop_loss, take_profit)
            return

        if r.get("retCode") != 0:
            state.set_alert(f"GAGAL ENTRY {symbol}: retCode={r.get('retCode')} {r.get('retMsg')}")
            log(f"BOT: GAGAL ENTRY {symbol}: retCode={r.get('retCode')}")
            # order ditolak — pastikan tidak ada posisi terlanjur terbuka
            time.sleep(1)
            cur = pm.get_position()
            if isinstance(cur, dict) and cur["symbol"] == symbol:
                log(f"BOT: entry ditolak tapi posisi {symbol} terbuka. Emergency close.")
                _emergency_close(symbol)
            return

        # VERIFIKASI SL/TP exchange-side (requirement: jangan anggap sukses hanya dari retCode)
        time.sleep(1)
        ok, msg = _verify_sl_tp(symbol, action, expected_qty=qty)
        if not ok:
            log(f"BOT: SL/TP verifikasi GAGAL {symbol}: {msg}. EMERGENCY CLOSE.")
            state.set_alert(f"SL/TP verifikasi gagal {symbol}: {msg}. Emergency close.")
            _emergency_close(symbol)
            return

        log(f"{action.upper()} {symbol} {qty:.6f} @ {entry} | SL={stop_loss} TP={take_profit} | risk={risk_pct*100:.2f}% | SL/TP verified")

    rm.start_trailing(entry, side=action)
    state.add_trade({"t": time.strftime("%H:%M:%S"), "action": f"{action.upper()} {symbol}",
                     "qty": f"{qty:.6f}", "price": entry, "pnl": 0})
    state.update(position={"side": action, "qty": qty, "entry": entry, "symbol": symbol},
                 highest_price=entry, lowest_price=entry, trailing_pct=rm.trailing_pct())


def _analyze_candidates(top_candidates):
    """Kirim kandidat terbaik ke AI (sesuai memory gate)."""
    mem = candidate_memory.memory
    analyzed = 0
    ai_results = []
    for c in top_candidates[:scanner.AI_CANDIDATES]:
        sym = c["symbol"]
        score = c["score"]
        should, why = mem.should_analyze(sym, score)
        if not should:
            continue
        try:
            cand_data = scanner.enrich_full(sym, c)
        except Exception:
            cand_data = {"candidate": c, "multi_timeframe": {}}
        try:
            res = ai.analyze_candidate(cand_data)
            action = res.get("action", "hold")
            conf = int(res.get("confidence", 0))
            reason = res.get("reason", "")
            setup = res.get("setup_type", "other")
            mem.record_ai(sym, score, action)
            analyzed += 1
            ai_results.append({"symbol": sym, "action": action, "confidence": conf,
                               "reason": reason, "setup": setup})
            log(f"AI: {sym} {action.upper()} confidence={conf} setup={setup} reason={reason}")

            if action in ("long", "short") and conf >= AI_CONFIDENCE_MIN:
                state.update(ai_results=ai_results)
                return res, sym, c
        except Exception as e:
            log(f"AI error {sym}: {e}")
    state.update(ai_results=ai_results)
    return None, None, None


def _verify_protection(symbol, direction, label):
    """Setelah update SL (trailing/BE), verify SL/TP masih terpasang di exchange.
    Jika protection hilang → emergency close."""
    time.sleep(1)
    ok, msg = _verify_sl_tp(symbol, direction)
    if not ok:
        log(f"BOT: {label} SL update {symbol} menyebabkan protection hilang ({msg}). Emergency close.")
        state.set_alert(f"{label} SL update {symbol} gagal verify: {msg}. Emergency close.")
        _emergency_close(symbol)


def _manage_position(rm, pos):
    """Kelola posisi aktif: trailing + break-even via move SL (exchange-side)."""
    sym = pos.get("symbol", "BTCUSDT")
    side = pos.get("side", "Buy")
    direction = "long" if side == "Buy" else "short"
    entry = pos.get("entry", 0)
    qty = pos.get("qty", 0)

    try:
        price = float(bc.get_ticker(sym)["result"]["list"][0]["lastPrice"])
    except Exception:
        return

    if rm.entry_price is None:
        rm.start_trailing(entry, side=direction)
    if rm.side != direction:
        rm.start_trailing(entry, side=direction)

    # update trailing peak/trough
    rm.check_breakeven(price)
    rm.check_trailing(price)
    state.update(highest_price=rm.highest_price, lowest_price=rm.lowest_price,
                 trailing_pct=rm.trailing_pct())

    exit_reason = None
    if rm.check_time_stop():
        exit_reason = "time-stop"
    elif rm.check_trailing(price):
        exit_reason = "trailing"

    # break-even: pindah SL ke entry (exchange-side)
    if rm.breakeven_active:
        sl_price = entry
        if not DRY_RUN:
            try:
                bc.set_trading_stop(sym, side, stop_loss=sl_price)
                _verify_protection(sym, direction, "break-even")
            except Exception as e:
                log(f"Set break-even SL error: {e}")
    else:
        # trailing: geser SL lebih ketat sesuai trailing_pct (exchange-side)
        try:
            tsl_pct = rm.trailing_pct()
            if direction == "long":
                trail_sl = rm.highest_price * (1 - tsl_pct)
            else:
                trail_sl = rm.lowest_price * (1 + tsl_pct)
            # hanya geser ke arah yang menguntungkan
            if direction == "long" and trail_sl > entry:
                if not DRY_RUN:
                    bc.set_trading_stop(sym, side, stop_loss=trail_sl)
                    _verify_protection(sym, direction, "trailing")
            elif direction == "short" and trail_sl < entry:
                if not DRY_RUN:
                    bc.set_trading_stop(sym, side, stop_loss=trail_sl)
                    _verify_protection(sym, direction, "trailing")
        except Exception as e:
            log(f"Set trailing SL error: {e}")

    if exit_reason:
        if DRY_RUN:
            log(f"[DRY-RUN] {exit_reason} hit {sym} @ {price}. CLOSE position.")
        else:
            r = bc.close_position(sym, side, qty)
            if r.get("retCode") != 0:
                state.set_alert(f"GAGAL TUTUP {sym}: retCode={r.get('retCode')}")
            log(f"{exit_reason} hit {sym} @ {price}. CLOSE position.")
            # verifikasi posisi benar-benar tertutup
            time.sleep(1)
            detail = pm.get_position_detail(sym)
            if isinstance(detail, dict):
                log(f"BOT: posisi {sym} masih aktif ({detail['side']} qty={detail['qty']}) setelah close. Akan retry next loop.")
                return  # jangan catat PnL/reset trailing sebelum benar tertutup
        pnl = (price - entry) / entry * 100 if entry else 0
        if direction == "short":
            pnl = -pnl
        state.add_trade({"t": time.strftime("%H:%M:%S"), "action": f"CLOSE {sym} ({direction})",
                         "qty": qty, "price": price, "pnl": round(pnl, 2)})
        rm.register_result(pnl > 0)
        rm.reset_trailing()
        state.update(position=None, highest_price=None, lowest_price=None)


def run():
    global SELL_REQUEST
    rm = RiskManager()
    balance = get_balance()
    rm.on_new_day(balance)
    day_high_balance = max(balance, rm.daily_start_balance or balance)
    consecutive_errors = 0
    last_scan = 0

    mode = "DRY-RUN (AMAN)" if DRY_RUN else "LIVE (DANA ASLI)"
    state.update(running=True, dry_run=DRY_RUN, balance=balance,
                 daily_start_balance=rm.daily_start_balance,
                 risk_pct=rm.current_risk(),
                 started_at=time.strftime("%Y-%m-%d %H:%M:%S"))
    state.clear_alert()
    log(f"Bot start [{mode}] FUTURES. Balance: {balance} USDT. Risk: {rm.current_risk()*100:.2f}%")

    _hold_suspend()

    if not DRY_RUN:
        try:
            bc.set_position_mode_one_way()
            log("Position mode: one-way (maks 1 posisi per symbol).")
        except Exception as e:
            log(f"Set position mode warning: {e}")
        try:
            bc.set_leverage(LEVERAGE)
            log(f"Leverage set: {LEVERAGE}x (isolated).")
        except Exception as e:
            log(f"Set leverage warning: {e}")

        # startup reconciliation: kalau ada posisi tertinggal (restart), jangan buka posisi baru
        try:
            # #4: cancel orphan orders dari run sebelumnya agar tidak konflik
            try:
                orders = bc.get_open_orders()
                if orders.get("retCode") == 0:
                    active = [o for o in orders.get("result", {}).get("list", [])
                              if o.get("orderStatus") in ("New", "PartiallyFilled")]
                    if active:
                        log(f"Startup: {len(active)} orphan order ditemukan, cancel.")
                        bc.cancel_all_orders()
            except Exception as e:
                log(f"Startup: cek orphan orders error: {e}")

            pos = pm.get_position()
            if isinstance(pos, dict):
                sym = pos["symbol"]
                direction = "long" if pos["side"] == "Buy" else "short"
                log(f"Startup reconciliation: posisi aktif {sym} {pos['side']} qty={pos['qty']}. Ambil alih monitoring.")
                # #12: verifikasi protection (SL/TP) ada di exchange sebelum lanjut trading.
                ok, msg = _verify_sl_tp(sym, direction)
                if not ok:
                    log(f"Startup: posisi {sym} TANPA protection terverifikasi ({msg}).")
                    if pos.get("stop_loss") is None or pos.get("take_profit") is None:
                        # tidak ada SL/TP sama sekali → pasang ulang dari entry, gagal → close
                        try:
                            sl = pos["entry"] * (1 - 0.005) if direction == "long" else pos["entry"] * (1 + 0.005)
                            tp = pos["entry"] * (1 + 0.01) if direction == "long" else pos["entry"] * (1 - 0.01)
                            bc.set_trading_stop(sym, pos["side"], stop_loss=sl, take_profit=tp)
                            log(f"Startup: SL/TP {sym} dipasang ulang (SL={sl} TP={tp}).")
                        except Exception as e:
                            log(f"Startup: gagal pasang SL/TP {sym}: {e}. Emergency close.")
                            _emergency_close(sym)
                rm.start_trailing(pos["entry"], side=direction)
                state.update(position={"side": pos["side"], "qty": pos["qty"],
                                       "entry": pos["entry"], "symbol": sym})
        except Exception as e:
            log(f"Startup reconciliation error: {e}")

    while not SHUTDOWN:
        if SELL_REQUEST:
            log("Signal CLOSE diterima (manual dari dashboard).")
            sell_all(rm)
            SELL_REQUEST = False
            continue

        if rm.in_cooldown():
            state.update(cooldown=True)
            log("Cooldown aktif (3 loss beruntun). Resume nanti.")
            sleep_check(30)
            continue

        try:
            balance = get_balance()
            rm.on_new_day(balance)

            if balance > day_high_balance:
                day_high_balance = balance
            drop_from_high = (day_high_balance - balance) / day_high_balance if day_high_balance > 0 else 0
            if drop_from_high >= CRASH_PCT:
                state.set_alert(
                    f"ASET TURUN DRASTIS {drop_from_high*100:.1f}% dari puncak hari ini! "
                    f"Bot otomatis tutup semua & berhenti."
                )
                log(f"CIRCUIT BREAKER: saldo turun {drop_from_high*100:.1f}%. Close all & stop.")
                sell_all(rm)
                break

            state.update(balance=balance,
                         daily_start_balance=rm.daily_start_balance,
                         daily_pnl_pct=rm.daily_pnl_pct(balance),
                         daily_pnl_usdt=balance - (rm.daily_start_balance or balance),
                         risk_pct=rm.current_risk(),
                         consecutive_losses=rm.consecutive_losses,
                         cooldown=False)
            _save_daily(balance, rm)
            consecutive_errors = 0

            if rm.daily_loss_hit(balance):
                state.set_alert(f"Daily loss -1% tercapai ({rm.daily_pnl_pct(balance)*100:.2f}%). Bot stop hari ini.")
                log(f"Daily loss -1% tercapai ({rm.daily_pnl_pct(balance)*100:.2f}%). Stop hari ini.")
                sleep_check(60)
                continue

            pos = get_position_info()
            state.update(position=pos)
            has_pos = isinstance(pos, dict)
            pos_unknown = pos == "UNKNOWN"

            if pos_unknown:
                # status tidak diketahui → jangan entry, jangan kelola. Coba lagi next loop.
                log("BOT: status posisi UNKNOWN (API error). Skip entry/monitor sampai jelas.")
                sleep_check(10)
                continue

            # === SCAN + entry hanya jika TIDAK ada posisi ===
            if not has_pos and time.time() - last_scan >= SCAN_INTERVAL:
                last_scan = time.time()
                try:
                    result = scanner.scan()
                    for l in result["log"]:
                        log(l)
                    state.update(scanned_pairs=len(result.get("candidates", [])))
                    top = result.get("top", [])
                    cand_list = [{"symbol": c["symbol"], "price": c["price"],
                                  "price24hPcnt": c["price24hPcnt"], "score": c["score"],
                                  "reasons": c["reasons"]} for c in top]
                    state.update(candidates=cand_list)
                    res, sym, c = _analyze_candidates(top)
                    if res and sym:
                        checks = guard.build_guard_checks(rm, balance,
                                                          {"price": c["price"], "orderbook": {}, "klines_15m": []},
                                                          "none", int(res.get("confidence", 0)))
                        should_block, alerts, warns = guard.evaluate(checks)
                        if alerts:
                            for m in alerts:
                                log(f"[GUARD] {m}")
                        if should_block:
                            state.set_alert(" | ".join(alerts))
                            log("BOT: Rejected — guard kejanggalan.")
                        elif res.get("action") in ("long", "short") and int(res.get("confidence", 0)) >= AI_CONFIDENCE_MIN:
                            _try_entry(rm, balance, sym, c["price"], res)
                except Exception as e:
                    log(f"Scan error: {e}")

            # === POSISI AKTIF: kelola trailing/exit ===
            elif has_pos:
                _manage_position(rm, pos)

        except Exception as e:
            consecutive_errors += 1
            log(f"Error: {e}")
            if consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
                state.set_alert(
                    f"TERLALU BANYAK ERROR ({consecutive_errors}x berturut): {e}. "
                    f"Bot berhenti untuk keamanan."
                )
                log(f"STOP: {consecutive_errors} error berturut-turut. Bot berhenti.")
                break

        sleep_check(SCAN_INTERVAL)

    _cleanup(rm)


def _cleanup(rm):
    _release_suspend()
    state.update(running=False)
    log("Cleanup: cancel semua order...")
    if not DRY_RUN:
        try:
            pm.cancel_all()
        except Exception as e:
            log(f"Cancel order error: {e}")
    else:
        log("[DRY-RUN] cancel order (skip)")

    if not DRY_RUN:
        sell_all(rm)

    log("Bot berhenti.")
    sys.exit(0)


if __name__ == "__main__":
    run()
