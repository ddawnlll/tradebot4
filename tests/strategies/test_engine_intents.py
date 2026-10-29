"""Engine intent construction: the single-TP fallback for small accounts."""

from __future__ import annotations

import pytest

import bot.core.engine as engine_module
from bot.core.engine import DecisionEngine, EngineConfig
from bot.core.models import InstrumentSpec, PortfolioSnapshot, Side
from bot.core.risk.sizing import RiskDecision
from bot.core.strategies.base import EntryContext, EntryDecision, EntryProposal, StrategyBase


class DummyStrategy(StrategyBase):
    name = "dummy"
    version = "test"
    needs_range_setup = False

    def evaluate_entry(self, ctx: EntryContext) -> EntryDecision:
        return EntryDecision(None)


def test_two_legs_collapse_to_one_when_notional_is_too_small(monkeypatch: pytest.MonkeyPatch) -> None:
    spec = InstrumentSpec(
        symbol="X",
        base_asset="X",
        price_tick=0.01,
        qty_step=0.001,
        min_qty=0.001,
        min_notional=5.0,
    )
    engine = DecisionEngine(DummyStrategy(), {"X": spec}, EngineConfig(require_context=False))
    engine.last_snapshot = PortfolioSnapshot(
        ts_ms=0, equity=1000, balance=1000, available_margin=1000, unrealized_pnl=0, used_margin=0
    )
    # A quantity whose halves are each below the 5 USDT minimum notional.
    monkeypatch.setattr(
        engine_module,
        "size_position",
        lambda **_: RiskDecision(True, None, 0.04, 0.5, 1.0, 1.0),
    )
    proposal = EntryProposal(
        side=Side.LONG,
        entry_price=100.0,
        stop_price=99.0,
        take_profits=(101.0, 102.0),
        reason="test",
    )
    intent, reject, _ = engine._build_intent(engine.states["X"], proposal, None, 60_000)
    assert reject is None
    assert intent is not None
    assert len(intent.take_profits) == 1
    assert intent.legs == ()
    assert engine.positions["X"].tp2_price is None
