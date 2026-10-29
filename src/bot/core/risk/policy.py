"""Account-level risk policy: daily loss, losing streak, drawdown kill switch.

The policy is event driven and framework independent. It owns no orders; it
only answers "may a new entry be opened, and at what risk fraction".
"""

from __future__ import annotations

from dataclasses import dataclass

from bot.core.models import DAY_MS, PortfolioSnapshot, RejectReason, RiskState


@dataclass(frozen=True, slots=True)
class RiskPolicyConfig:
    daily_loss_limit_frac: float = 0.15
    consecutive_losses_limit: int = 4
    cooldown_ms: int = 24 * 60 * 60 * 1000
    kill_switch_drawdown_frac: float = 0.50
    max_positions: int = 1
    max_open_risk_frac: float = 0.10
    risk_fraction: float = 0.05
    max_leverage: float = 10.0
    stale_data_ms: int = 180_000


@dataclass(slots=True)
class RiskPolicy:
    config: RiskPolicyConfig
    state: RiskState = RiskState.ACTIVE
    day_start_equity: float = 0.0
    current_day: int = -1
    peak_equity: float = 0.0
    consecutive_losses: int = 0
    cooldown_until_ms: int = 0
    halt_reason: str | None = None
    open_risk_usdt: float = 0.0

    def on_equity(self, ts_ms: int, equity: float) -> None:
        day = ts_ms // DAY_MS
        if self.current_day != day:
            self.current_day = day
            self.day_start_equity = equity
        self.peak_equity = max(self.peak_equity, equity)
        if self.peak_equity > 0 and equity <= self.peak_equity * (1.0 - self.config.kill_switch_drawdown_frac):
            self._halt("KILL_SWITCH_DRAWDOWN")
            return
        if self.state is RiskState.HALTED:
            return
        if self.day_start_equity > 0 and equity <= self.day_start_equity * (1.0 - self.config.daily_loss_limit_frac):
            self.state = RiskState.REDUCING
        elif ts_ms >= self.cooldown_until_ms and self.state is RiskState.REDUCING:
            self.state = RiskState.ACTIVE

    def on_trade_closed(self, ts_ms: int, net_pnl: float) -> None:
        if net_pnl < 0:
            self.consecutive_losses += 1
            if self.consecutive_losses >= self.config.consecutive_losses_limit:
                self.cooldown_until_ms = max(self.cooldown_until_ms, ts_ms + self.config.cooldown_ms)
                self.state = RiskState.REDUCING
        else:
            self.consecutive_losses = 0

    def on_position_opened(self, risk_usdt: float) -> None:
        self.open_risk_usdt += risk_usdt

    def on_position_closed(self, risk_usdt: float) -> None:
        self.open_risk_usdt = max(0.0, self.open_risk_usdt - risk_usdt)

    def allow_entry(self, ts_ms: int, snapshot: PortfolioSnapshot, *, data_ts_ms: int | None = None) -> str | None:
        """Return a reject reason, or ``None`` when an entry is allowed."""
        if self.state is RiskState.HALTED:
            return self.halt_reason or RejectReason.RISK_POLICY.value
        if data_ts_ms is not None and ts_ms - data_ts_ms > self.config.stale_data_ms:
            return RejectReason.STALE_DATA.value
        if ts_ms < self.cooldown_until_ms:
            return RejectReason.COOLDOWN.value
        if self.state is RiskState.REDUCING:
            # A cooldown or a daily-loss reduction can expire; re-enable only
            # when the day's loss is back inside the limit.
            daily_ok = self.day_start_equity <= 0 or snapshot.equity > self.day_start_equity * (
                1.0 - self.config.daily_loss_limit_frac
            )
            if ts_ms >= self.cooldown_until_ms and daily_ok:
                self.state = RiskState.ACTIVE
            else:
                return RejectReason.RISK_POLICY.value
        if len(snapshot.open_positions) >= self.config.max_positions:
            return RejectReason.MAX_POSITIONS.value
        oir = self.open_risk_usdt / snapshot.equity if snapshot.equity > 0 else float("inf")
        if oir >= self.config.max_open_risk_frac:
            return RejectReason.RISK_POLICY.value
        return None

    def risk_fraction(self) -> float:
        return self.config.risk_fraction if self.state is RiskState.ACTIVE else 0.0

    def resume(self, *, human_approved: bool = False) -> None:
        if self.state is RiskState.HALTED and not human_approved:
            raise PermissionError("HALTED requires human approval to resume")
        self.state = RiskState.ACTIVE
        self.halt_reason = None
        self.consecutive_losses = 0
        self.cooldown_until_ms = 0

    def _halt(self, reason: str) -> None:
        self.state = RiskState.HALTED
        self.halt_reason = reason

    def snapshot(self) -> dict:
        return {
            "state": self.state.value,
            "day_start_equity": self.day_start_equity,
            "peak_equity": self.peak_equity,
            "consecutive_losses": self.consecutive_losses,
            "cooldown_until_ms": self.cooldown_until_ms,
            "open_risk_usdt": self.open_risk_usdt,
            "halt_reason": self.halt_reason,
        }
