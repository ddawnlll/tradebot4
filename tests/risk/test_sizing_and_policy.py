"""Position sizing and account risk policy."""

from __future__ import annotations

import pytest

from bot.core.models import DAY_MS, InstrumentSpec, PortfolioSnapshot, RejectReason, RiskState, Side
from bot.core.risk.policy import RiskPolicy, RiskPolicyConfig
from bot.core.risk.sizing import SizingConfig, liquidation_price, size_position, split_legs

SPEC = InstrumentSpec(
    symbol="TESTUSDT",
    base_asset="TEST",
    price_tick=0.01,
    qty_step=0.1,
    min_qty=0.1,
    min_notional=5.0,
    maker_fee=0.0002,
    taker_fee=0.0005,
    max_leverage=10.0,
)


def snap(equity: float = 20.0) -> PortfolioSnapshot:
    return PortfolioSnapshot(ts_ms=0, equity=equity, balance=equity, available_margin=equity, unrealized_pnl=0.0, used_margin=0.0)


def test_sizing_respects_risk_including_fees() -> None:
    d = size_position(equity=20, available_margin=20, entry_price=100, stop_price=98, spec=SPEC, side=Side.LONG)
    assert d.approved
    assert d.quantity > 0
    # Loss at stop plus fees must not exceed the 1 USDT budget by much.
    loss = d.quantity * 2 + d.quantity * (100 + 98) * SPEC.taker_fee + d.quantity * 100 * 0.0002 * 2
    assert loss <= 1.0 + 1e-6
    assert d.risk_usdt <= 1.0 + 1e-9


def test_min_notional_rejection() -> None:
    fine = InstrumentSpec(symbol="FINE", base_asset="FINE", price_tick=0.01, qty_step=0.001, min_qty=0.001, min_notional=5.0)
    # risk 1 USDT with a 20% stop -> ~4.85 USDT notional, below the 5 USDT minimum.
    d = size_position(equity=20, available_margin=20, entry_price=100, stop_price=80, spec=fine, side=Side.LONG)
    assert not d.approved
    assert d.reason == RejectReason.MIN_NOTIONAL.value


def test_leverage_cap() -> None:
    cfg = SizingConfig(max_leverage=2.0)
    d = size_position(equity=20, available_margin=20, entry_price=100, stop_price=99.5, spec=SPEC, side=Side.LONG, config=cfg)
    assert not d.approved
    assert d.reason in (RejectReason.LEVERAGE.value, RejectReason.MIN_NOTIONAL.value)


def test_liquidation_buffer() -> None:
    # A 9x position with a 1% stop: liquidation is closer than the stop.
    d = size_position(
        equity=100,
        available_margin=100,
        entry_price=100,
        stop_price=99.0,
        spec=SPEC,
        side=Side.LONG,
        config=SizingConfig(max_leverage=10, maintenance_margin_rate=0.005),
    )
    assert d.approved or d.reason in (RejectReason.LEVERAGE.value, RejectReason.LIQUIDATION_BUFFER.value)
    liq = liquidation_price(100, 10, Side.LONG, 0.005)
    assert liq == pytest.approx(90.5)


def test_split_legs_respects_minimums() -> None:
    fine = InstrumentSpec(symbol="FINE", base_asset="FINE", price_tick=0.01, qty_step=0.001, min_qty=0.001, min_notional=5.0)
    assert split_legs(0.10, fine, 100, 2) == pytest.approx([0.05, 0.05])
    assert split_legs(0.04, fine, 100, 2) is None  # each leg 2 USDT < 5 USDT minimum
    assert split_legs(0.001, fine, 100, 2) is None


def test_risk_policy_daily_loss_reduces() -> None:
    p = RiskPolicy(RiskPolicyConfig(daily_loss_limit_frac=0.15))
    p.on_equity(0, 20.0)
    p.on_equity(1000, 20.0)
    p.on_equity(2000, 16.0)  # -20% on the day
    assert p.state is RiskState.REDUCING
    assert p.allow_entry(2000, snap(16.0)) == RejectReason.RISK_POLICY.value


def test_risk_policy_losing_streak_cooldown() -> None:
    p = RiskPolicy(RiskPolicyConfig(consecutive_losses_limit=3, cooldown_ms=DAY_MS))
    for i in range(3):
        p.on_trade_closed(i, -0.5)
    assert p.state is RiskState.REDUCING
    assert p.allow_entry(10, snap()) == RejectReason.COOLDOWN.value
    assert p.allow_entry(DAY_MS + 11, snap()) is None


def test_kill_switch_requires_human_approval() -> None:
    p = RiskPolicy(RiskPolicyConfig(kill_switch_drawdown_frac=0.5))
    p.on_equity(0, 20.0)
    p.on_equity(1, 9.0)
    assert p.state is RiskState.HALTED
    with pytest.raises(PermissionError):
        p.resume()
    p.resume(human_approved=True)
    assert p.state is RiskState.ACTIVE


def test_stale_data_blocks_entries() -> None:
    p = RiskPolicy(RiskPolicyConfig(stale_data_ms=180_000))
    assert p.allow_entry(1_000_000, snap(), data_ts_ms=1_000_000 - 200_000) == RejectReason.STALE_DATA.value
