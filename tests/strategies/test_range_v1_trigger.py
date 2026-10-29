"""Entry trigger must require a close back inside the zone."""

from __future__ import annotations

from bot.core.models import Bar, MarketRegime, RegimeState, Side, Zone
from bot.core.strategies.base import EntryContext
from bot.core.strategies.range_v1 import RangeV1Config, RangeV1Strategy


def _ctx(close: float, low: float, high: float, side_support: float = 100.0) -> EntryContext:
    support = Zone("SUPPORT", side_support - 0.5, side_support + 0.5, touches=3, first_touch_ms=0, last_touch_ms=1)
    resistance = Zone("RESISTANCE", 110.0, 111.0, touches=3, first_touch_ms=0, last_touch_ms=1)
    bar = Bar("X", 0, 60_000, open=close, high=high, low=low, close=close, volume=1.0, quote_volume=100.0)
    setup = type("S", (), {})()
    setup.support = support
    setup.resistance = resistance
    setup.mid = 105.5
    setup.width = 10.5
    setup.atr = 1.0
    setup.score = 0.7
    setup.score_components = {}
    setup.breakout_risk = 0.1
    setup.squeeze_risk = 0.1
    setup.ts_ms = 60_000
    setup.symbol = "X"
    setup.width_pct = 0.1
    setup.duration_bars = 100
    regime = MarketRegime(RegimeState.RANGE, 0.5, 50, 0.01, 0.1)
    state = type("St", (), {})()
    state.symbol = "X"
    state.atr = 1.0
    state.bars_entry = [bar] * 100
    state.last_1m = bar
    state.spec = type("Spec", (), {"taker_fee": 0.0005, "price_tick": 0.01})()
    state.zones = type("Z", (), {"sweep_buffer": lambda *a, **k: 0.3})()
    state.median_entry_quote_volume = 0.0
    state.config = type("C", (), {"entry_tf_ms": 900_000})()
    return EntryContext(state=state, bar=bar, setup=setup, regime=regime, higher_regime=None, portfolio=None, ts_ms=60_000)


def test_long_requires_close_inside_zone() -> None:
    strat = RangeV1Strategy(RangeV1Config(entry_tf_bars_min=10))
    # close 103 is above the support zone (100.5) and below the resistance: not an entry
    ctx = _ctx(close=103.0, low=100.0, high=103.2)
    assert strat.evaluate_entry(ctx).proposal is None
    # close 100.4 is inside the support zone with a rejection wick
    ctx = _ctx(close=100.4, low=99.6, high=100.8)
    decision = strat.evaluate_entry(ctx)
    assert decision.proposal is not None
    assert decision.proposal.side is Side.LONG
    assert decision.proposal.stop_price < 99.5


def test_short_requires_close_inside_zone() -> None:
    strat = RangeV1Strategy(RangeV1Config(entry_tf_bars_min=10))
    ctx = _ctx(close=107.0, low=106.8, high=111.2)
    assert strat.evaluate_entry(ctx).proposal is None
    ctx = _ctx(close=110.4, low=110.0, high=111.2)
    decision = strat.evaluate_entry(ctx)
    assert decision.proposal is not None
    assert decision.proposal.side is Side.SHORT
