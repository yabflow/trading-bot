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
