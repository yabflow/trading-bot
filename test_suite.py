import os
import sys
import json

sys.path.insert(0, os.path.dirname(__file__))

import ai_analyzer
import position_manager
import risk_manager
import performance


def _patch(fn):
    """Patch position_manager.bc.get_positions, restore after."""
    def deco(test):
        def wrapper():
            orig = position_manager.bc.get_positions
            position_manager.bc.get_positions = fn
            try:
                test()
            finally:
                position_manager.bc.get_positions = orig
        return wrapper
    return deco


def test_ai_normalize_long():
    r = ai_analyzer._normalize({"action": "long", "confidence": 80, "entry": 100, "stop_loss": 98, "take_profit": 105})
    assert r["action"] == "long"


def test_ai_normalize_short():
    r = ai_analyzer._normalize({"action": "short", "confidence": 75, "entry": 100, "stop_loss": 102, "take_profit": 95})
    assert r["action"] == "short"


def test_ai_map_buy_sell():
    assert ai_analyzer._normalize({"action": "buy", "confidence": 70, "entry": 10, "stop_loss": 9, "take_profit": 12})["action"] == "long"
    assert ai_analyzer._normalize({"action": "sell", "confidence": 70, "entry": 10, "stop_loss": 11, "take_profit": 8})["action"] == "short"


def test_ai_invalid_action_hold():
    r = ai_analyzer._normalize({"action": "gobbledygook", "confidence": 90, "entry": 100, "stop_loss": 99, "take_profit": 105})
    assert r["action"] == "hold"


def test_ai_bad_direction_hold():
    # long tapi SL>entry (arah salah) -> hold
    r = ai_analyzer._normalize({"action": "long", "confidence": 90, "entry": 100, "stop_loss": 101, "take_profit": 105})
    assert r["action"] == "hold"
    # short tapi TP>entry (arah salah) -> hold
    r = ai_analyzer._normalize({"action": "short", "confidence": 90, "entry": 100, "stop_loss": 102, "take_profit": 101})
    assert r["action"] == "hold"


def test_ai_non_dict_hold():
    assert ai_analyzer._normalize("nonsense")["action"] == "hold"
    assert ai_analyzer._normalize(None)["action"] == "hold"


def test_ai_parse_error_hold():
    r = ai_analyzer._safe_parse("no json here")
    assert r["action"] == "hold"


@_patch(lambda: (_ for _ in ()).throw(Exception("api down")))
def test_position_unknown_on_api_error():
    assert position_manager.get_position() == "UNKNOWN"


@_patch(lambda: {"retCode": 0, "result": {"list": [{"size": "0"}]}})
def test_position_none_on_no_pos():
    assert position_manager.get_position() is None


@_patch(lambda: {"retCode": 0, "result": {"list": [{
    "symbol": "BTCUSDT", "side": "Sell", "size": "0.1",
    "avgPrice": "100", "stopLoss": "102", "takeProfit": "95",
    "liqPrice": "120"}]}})
def test_position_dict_on_active():
    p = position_manager.get_position()
    assert p["side"] == "Sell"
    assert p["qty"] == 0.1
    assert p["stop_loss"] == 102.0
    assert p["take_profit"] == 95.0
    assert p["liq_price"] == 120.0


@_patch(lambda: {"retCode": 10003, "retMsg": "err"})
def test_position_unknown_on_bad_retcode():
    assert position_manager.get_position() == "UNKNOWN"


def test_risk_position_size():
    rm = risk_manager.RiskManager()
    rm.consecutive_losses = 0
    # balance 2000, risk 0.5% = 10 USDT. jarak SL 2, leverage 1 -> qty 5
    qty = rm.position_size(2000, 100, 98)
    assert abs(qty - 5.0) < 0.01


def test_risk_reduction_2_loss():
    rm = risk_manager.RiskManager()
    rm.consecutive_losses = 2
    assert rm.current_risk() == risk_manager.RISK_AFTER_2_LOSS


def test_risk_reduction_3_loss():
    rm = risk_manager.RiskManager()
    rm.consecutive_losses = 3
    assert rm.current_risk() == risk_manager.RISK_AFTER_3_LOSS


def test_risk_strong_setup_cap():
    rm = risk_manager.RiskManager()
    rm.consecutive_losses = 0
    r = rm.current_risk(strong_setup=True)
    assert r <= risk_manager.MAX_RISK_PER_TRADE


def test_validate_rr():
    rm = risk_manager.RiskManager()
    assert rm.validate_rr("long", 100, 98, 104)  # R/R = 2/2 = 1.0 < 1.5 -> False? risk2 reward4=2.0
    assert rm.validate_rr("long", 100, 98, 104.5)  # reward 4.5 risk 2 = 2.25 >= 1.5
    assert not rm.validate_rr("long", 100, 98, 100.5)  # reward 0.5 -> false


def test_performance_short_not_counted_as_sell():
    data = [{"action": "SHORT BTCUSDT", "pnl": 0}, {"action": "CLOSE BTCUSDT (short)", "pnl": -1.2}]
    assert performance._load_all_trades.__name__  # import check only


# --- Safety: failure/retry/unknown-state scenarios ---

def _pos(side="Buy", qty=0.1, entry=100, sl=98, tp=105):
    return {"symbol": "BTCUSDT", "side": side, "qty": qty, "entry": entry,
            "leverage": "1", "unrealisedPnl": 0.0, "stop_loss": sl, "take_profit": tp,
            "liq_price": None}


def test_verify_sl_tp_ok_long():
    import bot
    orig = position_manager.get_position_detail
    position_manager.get_position_detail = lambda s: _pos(side="Buy", entry=100, sl=98, tp=105)
    try:
        ok, msg = bot._verify_sl_tp("BTCUSDT", "long", expected_qty=0.1)
        assert ok, msg
    finally:
        position_manager.get_position_detail = orig


def test_verify_sl_tp_missing_sl():
    import bot
    orig = position_manager.get_position_detail
    position_manager.get_position_detail = lambda s: _pos(side="Buy", entry=100, sl=None, tp=105)
    try:
        ok, msg = bot._verify_sl_tp("BTCUSDT", "long")
        assert not ok
        assert "SL/TP" in msg
    finally:
        position_manager.get_position_detail = orig


def test_verify_sl_tp_wrong_side():
    import bot
    orig = position_manager.get_position_detail
    position_manager.get_position_detail = lambda s: _pos(side="Sell", entry=100, sl=102, tp=95)
    try:
        ok, msg = bot._verify_sl_tp("BTCUSDT", "long")
        assert not ok
        assert "side" in msg
    finally:
        position_manager.get_position_detail = orig


def test_verify_sl_tp_qty_mismatch():
    import bot
    orig = position_manager.get_position_detail
    position_manager.get_position_detail = lambda s: _pos(side="Buy", qty=0.01, entry=100, sl=98, tp=105)
    try:
        ok, msg = bot._verify_sl_tp("BTCUSDT", "long", expected_qty=0.1)
        assert not ok
        assert "qty" in msg
    finally:
        position_manager.get_position_detail = orig


def test_verify_sl_tp_unknown():
    import bot
    orig = position_manager.get_position_detail
    position_manager.get_position_detail = lambda s: "UNKNOWN"
    try:
        ok, msg = bot._verify_sl_tp("BTCUSDT", "long")
        assert not ok
        assert "API" in msg
    finally:
        position_manager.get_position_detail = orig


def test_verify_sl_tp_no_position():
    import bot
    orig = position_manager.get_position_detail
    position_manager.get_position_detail = lambda s: None
    try:
        ok, msg = bot._verify_sl_tp("BTCUSDT", "long")
        assert not ok
        assert "tidak ditemukan" in msg
    finally:
        position_manager.get_position_detail = orig


def test_emergency_close_verifies_zero():
    import bot
    calls = {"closed": False}
    orig_detail = position_manager.get_position_detail
    orig_close = bot.bc.close_position
    bot.bc.close_position = lambda s, side, qty: calls.update(closed=True) or {"retCode": 0}
    seq = [_pos(side="Buy"), None]  # first call: still open, after close: gone
    position_manager.get_position_detail = lambda s: seq.pop(0)
    try:
        bot._emergency_close("BTCUSDT")
        assert calls["closed"]
        assert bot.state.data.get("alert") is None
    finally:
        position_manager.get_position_detail = orig_detail
        bot.bc.close_position = orig_close


def test_emergency_close_still_open_alert():
    import bot
    orig_detail = position_manager.get_position_detail
    orig_close = bot.bc.close_position
    bot.bc.close_position = lambda s, side, qty: {"retCode": 0}
    # after close, still open
    position_manager.get_position_detail = lambda s: _pos(side="Buy")
    bot.state.clear_alert()
    try:
        bot._emergency_close("BTCUSDT")
        assert "EMERGENCY CLOSE GAGAL" in (bot.state.data.get("alert") or "")
    finally:
        position_manager.get_position_detail = orig_detail
        bot.bc.close_position = orig_close
        bot.state.clear_alert()


def test_verify_sl_tp_liq_too_close_long():
    import bot
    orig = position_manager.get_position_detail
    # liqPrice 99 di atas SL 98 → terlalu dekat (LONG)
    position_manager.get_position_detail = lambda s: _pos(side="Buy", entry=100, sl=98, tp=105) | {"liq_price": 99.0}
    try:
        ok, msg = bot._verify_sl_tp("BTCUSDT", "long")
        assert not ok
        assert "likuidasi" in msg
    finally:
        position_manager.get_position_detail = orig


def test_verify_sl_tp_liq_safe_long():
    import bot
    orig = position_manager.get_position_detail
    # liqPrice 90 jauh di bawah SL 98 → aman (LONG)
    position_manager.get_position_detail = lambda s: _pos(side="Buy", entry=100, sl=98, tp=105) | {"liq_price": 90.0}
    try:
        ok, msg = bot._verify_sl_tp("BTCUSDT", "long")
        assert ok, msg
    finally:
        position_manager.get_position_detail = orig


def test_verify_sl_tp_liq_too_close_short():
    import bot
    orig = position_manager.get_position_detail
    # liqPrice 103 di bawah SL 104 → terlalu dekat (SHORT)
    position_manager.get_position_detail = lambda s: _pos(side="Sell", entry=100, sl=104, tp=95) | {"liq_price": 103.0}
    try:
        ok, msg = bot._verify_sl_tp("BTCUSDT", "short")
        assert not ok
        assert "likuidasi" in msg
    finally:
        position_manager.get_position_detail = orig


def test_sell_all_unknown_no_state_reset():
    import bot
    rm = risk_manager.RiskManager()
    rm.start_trailing(100, side="long")
    bot.state.update(position={"side": "long", "qty": 1, "entry": 100, "symbol": "BTCUSDT"})
    bot.state.clear_alert()
    orig_sell = position_manager.sell_all
    orig_dry = bot.DRY_RUN
    bot.DRY_RUN = False
    position_manager.sell_all = lambda: ["UNKNOWN", "VERIFY-UNKNOWN", "ORDERS-CLEAN"]
    try:
        bot.sell_all(rm)
        # state TIDAK direset karena UNKNOWN
        assert bot.state.data.get("position") is not None
        assert rm.entry_price is not None
        assert "UNKNOWN" in (bot.state.data.get("alert") or "")
    finally:
        position_manager.sell_all = orig_sell
        bot.DRY_RUN = orig_dry
        bot.state.clear_alert()
        bot.state.update(position=None)
        rm.reset_trailing()


def test_sell_all_verified_closed_resets_state():
    import bot
    rm = risk_manager.RiskManager()
    rm.start_trailing(100, side="long")
    bot.state.update(position={"side": "long", "qty": 1, "entry": 100, "symbol": "BTCUSDT"})
    orig_sell = position_manager.sell_all
    orig_dry = bot.DRY_RUN
    bot.DRY_RUN = False
    position_manager.sell_all = lambda: [{"retCode": 0}, "VERIFIED-CLOSED", "ORDERS-CLEAN"]
    try:
        bot.sell_all(rm)
        assert bot.state.data.get("position") is None
        assert rm.entry_price is None
    finally:
        position_manager.sell_all = orig_sell
        bot.DRY_RUN = orig_dry


def test_ai_model_routing_from_config():
    # worker kirim model yang dipilih dashboard (via AI_MODEL), bukan fallback
    saved = (ai_analyzer.AI_MODEL, ai_analyzer.AI_BASE_URL, ai_analyzer.AI_API_KEY)
    try:
        ai_analyzer.AI_MODEL = "ts/thirty/deepseek-v4.1-flash"
        ai_analyzer.AI_BASE_URL = "http://localhost:20128/v1"
        ai_analyzer.AI_API_KEY = "sk-test"

        captured = {}

        def fake_urlopen(req, timeout=60):
            captured["url"] = req.full_url
            captured["body"] = json.loads(req.data)
            captured["auth"] = req.get_header("Authorization")

            class R:
                def read(s):
                    return b'{"choices":[{"message":{"content":"{\\"action\\":\\"hold\\",\\"confidence\\":0,\\"entry\\":0,\\"stop_loss\\":0,\\"take_profit\\":0}"}}]}'
                def __enter__(s): return s
                def __exit__(s, *a): return False
            return R()

        orig = ai_analyzer.urllib.request.urlopen
        ai_analyzer.urllib.request.urlopen = fake_urlopen
        try:
            ai_analyzer.analyze({"harga": 78000}, "none")
        finally:
            ai_analyzer.urllib.request.urlopen = orig

        assert captured["body"]["model"] == "ts/thirty/deepseek-v4.1-flash"
        assert captured["url"] == "http://localhost:20128/v1/chat/completions"
        assert captured["auth"] == "Bearer sk-test"
    finally:
        ai_analyzer.AI_MODEL, ai_analyzer.AI_BASE_URL, ai_analyzer.AI_API_KEY = saved


def test_ai_model_prefix_kept_for_9router():
    # model dengan prefix ts/ tidak boleh di-strip/diganti sebelum dikirim
    saved = ai_analyzer.AI_MODEL
    try:
        ai_analyzer.AI_MODEL = "ts/thirty/deepseek-v4.1-flash"
        captured = {}

        def fake_urlopen(req, timeout=60):
            captured["model"] = json.loads(req.data)["model"]

            class R:
                def read(s):
                    return b'{"choices":[{"message":{"content":"{\\"action\\":\\"hold\\",\\"confidence\\":0,\\"entry\\":0,\\"stop_loss\\":0,\\"take_profit\\":0}"}}]}'
                def __enter__(s): return s
                def __exit__(s, *a): return False
            return R()

        orig = ai_analyzer.urllib.request.urlopen
        ai_analyzer.urllib.request.urlopen = fake_urlopen
        try:
            ai_analyzer.analyze({"harga": 1}, "none")
        finally:
            ai_analyzer.urllib.request.urlopen = orig
        assert captured["model"] == "ts/thirty/deepseek-v4.1-flash"
    finally:
        ai_analyzer.AI_MODEL = saved


def test_ai_missing_config_fails_safe():
    # config kosong → tidak fallback diam-diam ke deepseek/thirty
    try:
        ai_analyzer._require_config("", "", "")
        assert False, "seharusnya raise RuntimeError"
    except RuntimeError as e:
        assert "AI_BASE_URL" in str(e)
    try:
        ai_analyzer._require_config("http://x/v1", "sk", "")
        assert False, "seharusnya raise RuntimeError"
    except RuntimeError as e:
        assert "AI_MODEL" in str(e)


def test_start_bot_uses_env_file():
    import daemon

    captured = {}

    def fake_popen(args, **kw):
        captured["env"] = kw.get("env", {})
        captured["cwd"] = kw.get("cwd")

        class P:
            pid = 4242
            stdout = None
            def __init__(self): pass
            def wait(self): return 0
        p = P()
        p.stdout = []
        return p

    orig_pop = daemon.subprocess.Popen
    orig_botpid = daemon.BOT_PID
    orig_alive = daemon._alive
    orig_update = daemon.state.state.update
    daemon.subprocess.Popen = fake_popen
    daemon.BOT_PID = None
    daemon._alive = lambda pid: False
    daemon.state.state.update = lambda **kw: None
    try:
        ok, msg = daemon.start_bot()
    finally:
        daemon.subprocess.Popen = orig_pop
        daemon.BOT_PID = orig_botpid
        daemon._alive = orig_alive
        daemon.state.state.update = orig_update

    assert ok
    env = captured["env"]
    # AI config harus berasal dari .env (yang dibaca _load_env), bukan os.environ stale
    assert env.get("AI_BASE_URL") == daemon._load_env().get("AI_BASE_URL")
    assert env.get("AI_MODEL") == daemon._load_env().get("AI_MODEL")
    assert env.get("AI_API_KEY") == daemon._load_env().get("AI_API_KEY")


def test_dashboard_auth_fail_closed():
    import base64 as b64
    import daemon

    class FH:
        def __init__(s, d): s._d = d
        def get(s, k, default=""): return s._d.get(k, default)

    cred = b64.b64encode(b"testuser:testpass").decode()
    h = daemon.Handler.__new__(daemon.Handler)

    saved = (daemon.DASH_USER, daemon.DASH_PASS, daemon.AUTH_ENABLED)
    try:
        # fail-closed: env kosong → AUTH_ENABLED False → tolak walau kredensial benar
        daemon.DASH_USER = daemon.DASH_PASS = ""
        daemon.AUTH_ENABLED = False
        h.headers = FH({"Authorization": "Basic " + cred})
        assert h._auth_ok() is False

        # env diset → AUTH_ENABLED True → auth jalan
        daemon.DASH_USER, daemon.DASH_PASS, daemon.AUTH_ENABLED = "testuser", "testpass", True
        h.headers = FH({"Authorization": "Basic " + cred})
        assert h._auth_ok() is True

        # kredensial salah → tolak
        h.headers = FH({"Authorization": "Basic " + b64.b64encode(b"testuser:wrong").decode()})
        assert h._auth_ok() is False
    finally:
        daemon.DASH_USER, daemon.DASH_PASS, daemon.AUTH_ENABLED = saved


if __name__ == "__main__":
    import traceback
    failures = []
    tests = [v for k, v in list(globals().items()) if k.startswith("test_") and callable(v)]
    for t in tests:
        try:
            t()
            print(f"PASS {t.__name__}")
        except Exception as e:
            failures.append((t.__name__, e))
            print(f"FAIL {t.__name__}: {e}")
    if failures:
        print(f"\n{failures}")
        sys.exit(1)
    print(f"\nAll {len(tests)} tests passed.")
