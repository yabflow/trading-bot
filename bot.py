import os
import signal
import sys
import time

import bybit_client as bc
import ai_analyzer as ai
import candidate_memory
import guard
import position_manager as pm
import scanner
from risk_manager import RiskManager
from state import state

SHUTDOWN = False
SELL_REQUEST = False
DRY_RUN = os.getenv("DRY_RUN", "0") == "1"

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

    risk_pct = rm.current_risk(confidence=confidence, strong_setup=strong_setup)
    side_str = "Buy" if action == "long" else "Sell"
    if DRY_RUN:
        log(f"[DRY-RUN] {action.upper()} {symbol} {qty:.6f} @ {entry} | SL={stop_loss} TP={take_profit} | risk={risk_pct*100:.2f}% conf={confidence}")
    else:
        r = bc.create_order(side_str, qty, symbol=symbol,
                            stop_loss=stop_loss, take_profit=take_profit)
        if r.get("retCode") != 0:
            state.set_alert(f"GAGAL ENTRY {symbol}: retCode={r.get('retCode')} {r.get('retMsg')}")
            log(f"BOT: GAGAL ENTRY {symbol}: retCode={r.get('retCode')}")
            return
        log(f"{action.upper()} {symbol} {qty:.6f} @ {entry} | SL={stop_loss} TP={take_profit} | risk={risk_pct*100:.2f}%")

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
            elif direction == "short" and trail_sl < entry:
                if not DRY_RUN:
                    bc.set_trading_stop(sym, side, stop_loss=trail_sl)
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

    if not DRY_RUN:
        try:
            from risk_manager import LEVERAGE
            bc.set_leverage(LEVERAGE)
            log(f"Leverage set: {LEVERAGE}x (isolated).")
        except Exception as e:
            log(f"Set leverage warning: {e}")

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
            has_pos = pos is not None

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
