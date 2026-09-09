import json
import os
import time

from dotenv import load_dotenv

load_dotenv()

INITIAL_CAPITAL = float(os.getenv("INITIAL_CAPITAL_USDT", "2000"))
RISK_PER_TRADE = float(os.getenv("RISK_PER_TRADE", "0.005"))
MAX_RISK_PER_TRADE = float(os.getenv("MAX_RISK_PER_TRADE", "0.01"))
RISK_AFTER_2_LOSS = float(os.getenv("RISK_AFTER_2_LOSS", "0.0035"))
RISK_AFTER_3_LOSS = float(os.getenv("RISK_AFTER_3_LOSS", "0.0025"))
MIN_RR = float(os.getenv("MIN_RR", "1.5"))
TRAILING_STOP = float(os.getenv("TRAILING_STOP", "0.005"))
DAILY_MAX_LOSS = float(os.getenv("DAILY_MAX_LOSS", "0.01"))
COOLDOWN_HOURS = float(os.getenv("COOLDOWN_HOURS", "6"))
BREAKEVEN_TRIGGER = float(os.getenv("BREAKEVEN_TRIGGER", "0.005"))  # profit 0.5% → SL ke entry
TIME_STOP_HOURS = float(os.getenv("TIME_STOP_HOURS", "4"))          # stuck >4 jam → jual
RISK_LOW_CONF = float(os.getenv("RISK_LOW_CONF", "0.0035"))         # conf 60-69 pakai risk kecil
LEVERAGE = float(os.getenv("LEVERAGE", "1"))                        # default 1x (tanpa amplifikasi)

STATE_FILE = os.path.join(os.path.dirname(__file__), "state.json")


class RiskManager:
    def __init__(self):
        self.consecutive_losses = 0
        self.daily_start_balance = None
        self.daily_start_day = None
        self.cooldown_until = 0
        self.highest_price = None   # untuk trailing long
        self.lowest_price = None    # untuk trailing short
        self.entry_price = None
        self.entry_time = None
        self.side = None            # "long" / "short"
        self.breakeven_active = False
        self._load()

    def _load(self):
        if os.path.exists(STATE_FILE):
            with open(STATE_FILE) as f:
                s = json.load(f)
            self.consecutive_losses = s.get("consecutive_losses", 0)
            self.daily_start_balance = s.get("daily_start_balance")
            self.daily_start_day = s.get("daily_start_day")
            self.cooldown_until = s.get("cooldown_until", 0)

    def save(self):
        with open(STATE_FILE, "w") as f:
            json.dump({
                "consecutive_losses": self.consecutive_losses,
                "daily_start_balance": self.daily_start_balance,
                "daily_start_day": self.daily_start_day,
                "cooldown_until": self.cooldown_until,
            }, f)

    def on_new_day(self, balance):
        today = time.strftime("%Y-%m-%d")
        if self.daily_start_day != today:
            self.daily_start_day = today
            self.daily_start_balance = balance
            self.save()

    def in_cooldown(self):
        return time.time() < self.cooldown_until

    def current_risk(self, strong_setup=False, confidence=None):
        if self.consecutive_losses >= 3:
            return RISK_AFTER_3_LOSS
        if self.consecutive_losses >= 2:
            return RISK_AFTER_2_LOSS
        if confidence is not None and confidence < 70:
            return RISK_LOW_CONF  # conf 60-69 → entry kecil
        if strong_setup:
            return min(MAX_RISK_PER_TRADE, 0.01)  # setup sangat kuat maks 1%
        return RISK_PER_TRADE

    def daily_pnl_pct(self, balance):
        if not self.daily_start_balance:
            return 0.0
        return (balance - self.daily_start_balance) / self.daily_start_balance

    def daily_loss_hit(self, balance):
        return self.daily_pnl_pct(balance) <= -DAILY_MAX_LOSS

    def register_result(self, won):
        if won:
            self.consecutive_losses = 0
        else:
            self.consecutive_losses += 1
            if self.consecutive_losses >= 3:
                self.cooldown_until = time.time() + COOLDOWN_HOURS * 3600
        self.save()

    def position_size(self, balance, entry, stop_loss, confidence=None, strong_setup=False):
        """Ukuran posisi (dalam base asset) berdasarkan risk & jarak SL.
        Futures: risk = (jarak SL / entry) x leverage x notional.
        qty = (balance * risk_pct) / (jarak SL * leverage)."""
        risk_amount = balance * self.current_risk(confidence=confidence, strong_setup=strong_setup)
        if entry == stop_loss or entry <= 0:
            return 0
        per_unit_risk = abs(entry - stop_loss) * LEVERAGE  # kerugian per kontrak jika SL
        if per_unit_risk <= 0:
            return 0
        qty = risk_amount / per_unit_risk
        return qty

    def validate_rr(self, side, entry, stop_loss, take_profit):
        """R/R >= MIN_RR untuk arah side."""
        risk = abs(entry - stop_loss)
        if risk <= 0:
            return False
        reward = abs(take_profit - entry)
        return reward / risk >= MIN_RR

    def start_trailing(self, entry_price, side="long"):
        self.highest_price = entry_price if side == "long" else None
        self.lowest_price = entry_price if side == "short" else None
        self.entry_price = entry_price
        self.entry_time = time.time()
        self.side = side
        self.breakeven_active = False

    def trailing_pct(self):
        if self.entry_price is None:
            return TRAILING_STOP
        if self.side == "short":
            if self.lowest_price is None:
                return TRAILING_STOP
            profit = (self.entry_price - self.lowest_price) / self.entry_price
        else:
            if self.highest_price is None:
                return TRAILING_STOP
            profit = (self.highest_price - self.entry_price) / self.entry_price
        if profit >= 0.10:
            return 0.017
        if profit >= 0.06:
            return 0.013
        if profit >= 0.03:
            return 0.01
        if profit >= 0.01:
            return 0.007
        return 0.005

    def check_trailing(self, current_price):
        """True jika harga balik melewati trailing % (long: turun, short: naik)."""
        if self.side == "short":
            if self.lowest_price is None:
                self.lowest_price = current_price
            if current_price < self.lowest_price:
                self.lowest_price = current_price
            rise = (current_price - self.lowest_price) / self.lowest_price
            return rise >= self.trailing_pct()
        else:
            if self.highest_price is None:
                self.highest_price = current_price
            if current_price > self.highest_price:
                self.highest_price = current_price
            drop = (self.highest_price - current_price) / self.highest_price
            return drop >= self.trailing_pct()

    def reset_trailing(self):
        self.highest_price = None
        self.lowest_price = None
        self.entry_price = None
        self.entry_time = None
        self.side = None
        self.breakeven_active = False

    def check_breakeven(self, current_price):
        """Setelah profit >= BREAKEVEN_TRIGGER, aktifkan break-even (SL = entry)."""
        if self.entry_price:
            if self.side == "short":
                if current_price <= self.entry_price * (1 - BREAKEVEN_TRIGGER):
                    self.breakeven_active = True
            else:
                if current_price >= self.entry_price * (1 + BREAKEVEN_TRIGGER):
                    self.breakeven_active = True
        return self.breakeven_active

    def check_time_stop(self):
        """Posisi stuck > TIME_STOP_HOURS → jual."""
        if self.entry_time is None:
            return False
        return (time.time() - self.entry_time) >= TIME_STOP_HOURS * 3600

    def stop_loss_price(self):
        """SL efektif: entry jika break-even aktif, else None (pakai trailing)."""
        return self.entry_price if self.breakeven_active else None
