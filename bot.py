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
SCAN_INTERVAL = int(os.getenv("SCAN_INTERVAL_SECONDS", "180"))   # scan 3 menit
AI_INTERVAL = int(os.getenv("AI_INTERVAL_SECONDS", "900"))       # AI minimal 15 menit
AI_CONFIDENCE_MIN = int(os.getenv("AI_CONFIDENCE_MIN", "60"))    # threshold entry

# HARD SAFETY: kalau DRY_RUN=1, pastikan tidak mungkin order nyata
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
    r = bc.get_wallet_balance()
    lst = r.get("result", {}).get("list", [])
    if lst:
        item = lst[0]
        return float(item.get("totalWalletBalance", 0) or item.get("totalEquity", 0) or 0)
    return 0.0


def get_position_info():
    r = bc.get_positions()
    lst = r.get("result", {}).get("list", [])
    for p in lst:
        size = float(p.get("size", 0) or 0)
        if size > 0:
            return {"side": p.get("side"), "qty": p.get("size"),
                    "entry": float(p.get("avgPrice", 0)),
                    "symbol": p.get("symbol")}
    return None


def sell_all(rm):
    log("MENJUAL SEMUA POSISI...")
    if not DRY_RUN:
        try:
            r = pm.sell_all()
            log(f"Sell all result: {r}")
        except Exception as e:
            log(f"Sell all error: {e}")
            state.set_alert(f"GAGAL JUAL POSISI: {e}")
    else:
        log("[DRY-RUN] sell semua (skip)")
    rm.reset_trailing()
    state.update(position=None, highest_price=None)


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


def _try_buy(rm, balance, symbol, price, signal_res):
    entry = float(signal_res.get("entry", price))
    stop_loss = float(signal_res.get("stop_loss", 0))
    take_profit = float(signal_res.get("take_profit", 0))
    confidence = int(signal_res.get("confidence", 0))

    if stop_loss <= 0 or take_profit <= 0:
        log(f"BOT: Rejected {symbol} — AI tidak beri SL/TP valid.")
        return

    # profit > fee: TP minimum harus >= SL x 1.5 (buffer fee 0.2% round-trip)
    risk = entry - stop_loss
    if risk <= 0:
        log(f"BOT: Rejected {symbol} — SL >= entry.")
        return
    min_tp = entry + risk * 1.5
    if take_profit < min_tp:
        log(f"BOT: Rejected {symbol} — TP {take_profit} < minimal {min_tp:.2f} (R/R 1.5).")
        return

    qty = rm.position_size(balance, entry, stop_loss, confidence)
    if qty <= 0:
        log(f"BOT: Rejected {symbol} — position size 0.")
        return

    risk_pct = rm.current_risk(confidence=confidence)
    if DRY_RUN:
        log(f"[DRY-RUN] BUY {symbol} {qty:.6f} @ {entry} | SL={stop_loss} TP={take_profit} | risk={risk_pct*100:.2f}% conf={confidence}")
    else:
        r = bc.create_order("Buy", qty, symbol=symbol)
        if r.get("retCode") != 0:
            state.set_alert(f"GAGAL BUY {symbol}: retCode={r.get('retCode')} {r.get('retMsg')}")
            log(f"BOT: GAGAL BUY {symbol}: retCode={r.get('retCode')}")
            return
        log(f"BUY {symbol} {qty:.6f} @ {entry} | SL={stop_loss} TP={take_profit} | risk={risk_pct*100:.2f}%")
    rm.start_trailing(entry)
    state.add_trade({"t": time.strftime("%H:%M:%S"), "action": f"BUY {symbol}",
                     "qty": f"{qty:.6f}", "price": entry, "pnl": 0})
    state.update(highest_price=entry, trailing_pct=rm.trailing_pct())


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
        # enrich data LENGKAP (multi-TF + S/R + volume profil + BTC context)
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

            if action == "buy" and conf >= AI_CONFIDENCE_MIN:
                state.update(ai_results=ai_results)
                return res, sym, c
        except Exception as e:
            log(f"AI error {sym}: {e}")
    state.update(ai_results=ai_results)
    return None, None, None


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
    log(f"Bot start [{mode}]. Balance: {balance} USDT. Risk: {rm.current_risk()*100:.2f}%")

    while not SHUTDOWN:
        if SELL_REQUEST:
            log("Signal SELL diterima (manual dari dashboard).")
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
                    f"Bot otomatis jual semua & berhenti."
                )
                log(f"CIRCUIT BREAKER: saldo turun {drop_from_high*100:.1f}%. Sell all & stop.")
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
            position = "long" if pos else "none"

            # === SCAN (market-wide) setiap SCAN_INTERVAL ===
            if position == "none" and time.time() - last_scan >= SCAN_INTERVAL:
                last_scan = time.time()
                try:
                    result = scanner.scan()
                    for l in result["log"]:
                        log(l)
                    state.update(scanned_pairs=len(result.get("candidates", [])))
                    # simpan top kandidat untuk dashboard
                    top = result.get("top", [])
                    cand_list = [{"symbol": c["symbol"], "price": c["price"],
                                  "price24hPcnt": c["price24hPcnt"], "score": c["score"],
                                  "reasons": c["reasons"]} for c in top]
                    state.update(candidates=cand_list)
                    # AI analysis untuk kandidat terbaik
                    res, sym, c = _analyze_candidates(top)
                    if res and sym:
                        # validasi guard sebelum entry
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
                        elif res.get("action") == "buy" and int(res.get("confidence", 0)) >= AI_CONFIDENCE_MIN:
                            _try_buy(rm, balance, sym, c["price"], res)
                except Exception as e:
                    log(f"Scan error: {e}")

            # === POSISI AKTIF: trailing stop ===
            elif position == "long":
                sym = pos.get("symbol", "BTCUSDT")
                try:
                    price = float(bc.get_ticker(sym)["result"]["list"][0]["lastPrice"])
                except Exception:
                    price = pos.get("entry", 0)
                entry = pos.get("entry", price)
                if rm.highest_price is None:
                    rm.start_trailing(entry)
                state.update(highest_price=rm.highest_price, trailing_pct=rm.trailing_pct())

                # break-even: profit >= 0.5% → aktifkan SL ke entry
                rm.check_breakeven(price)
                sl_price = rm.stop_loss_price()

                exit_reason = None
                # 1. time stop: stuck > 4 jam
                if rm.check_time_stop():
                    exit_reason = "time-stop"
                # 2. break-even: harga turun balik ke entry
                elif sl_price is not None and price <= sl_price and rm.breakeven_active:
                    exit_reason = "break-even"
                # 3. trailing stop
                elif rm.check_trailing(price):
                    exit_reason = "trailing"

                if exit_reason:
                    if DRY_RUN:
                        log(f"[DRY-RUN] {exit_reason} hit {sym} @ {price}. SELL all.")
                    else:
                        r = bc.create_order("Sell", "all", symbol=sym)
                        if r.get("retCode") != 0:
                            state.set_alert(f"GAGAL JUAL {sym}: retCode={r.get('retCode')}")
                        log(f"{exit_reason} hit {sym} @ {price}. SELL all.")
                    pnl = (price - entry) / entry * 100 if entry else 0
                    state.add_trade({"t": time.strftime("%H:%M:%S"), "action": f"SELL {sym}",
                                     "qty": pos.get("qty", "all"), "price": price,
                                     "pnl": round(pnl, 2)})
                    rm.register_result(True)
                    rm.reset_trailing()
                    state.update(position=None, highest_price=None)

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
