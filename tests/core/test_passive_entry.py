from types import SimpleNamespace

from bot.core.models import Bar, EntryType, MarketRegime, RangeSetup, RegimeState, Side, Zone
from bot.core.strategies.range_v1 import RangeV1Config, RangeV2PassiveStrategy


class _Zones:
    def sweep_buffer(self, mult, fallback):
        return fallback


def _setup() -> RangeSetup:
    support = Zone("SUPPORT", 0.95, 0.96, touches=2, first_touch_ms=0, last_touch_ms=1)
    resistance = Zone("RESISTANCE", 1.04, 1.05, touches=2, first_touch_ms=0, last_touch_ms=1)
    return RangeSetup(
        symbol="TESTUSDT",
        support=support,
        resistance=resistance,
        mid=1.0,
        width=0.10,
        width_pct=0.10,
        duration_bars=100,
        atr=0.01,
        breakout_risk=0.1,
        squeeze_risk=0.1,
        score=0.7,
    )


def _bar(close: float) -> Bar:
    return Bar(
        symbol="TESTUSDT",
        ts_open_ms=0,
        ts_close_ms=900_000,
        open=close - 0.001,
        high=close + 0.002,
        low=close - 0.002,
        close=close,
        volume=100.0,
        quote_volume=100 * close,
    )


def _ctx(close: float):
    bar = _bar(close)
    state = SimpleNamespace(
        symbol="TESTUSDT",
        bars_entry=[bar] * 60,
        median_entry_quote_volume=1e6,
        last_1m=bar,
        atr=0.01,
        zones=_Zones(),
    )
    regime = MarketRegime(RegimeState.RANGE, 0.5, 20, 0.02, 0.1, squeeze_risk=0.1)
    return SimpleNamespace(state=state, bar=bar, setup=_setup(), regime=regime)


def test_passive_long_places_limit_at_support_inner_edge():
    strategy = RangeV2PassiveStrategy(RangeV1Config(passive_entry_bars=4))
    decision = strategy.evaluate_entry(_ctx(0.97))
    proposal = decision.proposal
    assert proposal is not None, decision.detail
    assert proposal.side is Side.LONG
    assert proposal.entry_type is EntryType.LIMIT_POST_ONLY
    assert proposal.post_only is True
    assert proposal.hold_bars == 4
    assert proposal.entry_price == 0.96
    assert proposal.stop_price < 0.95
    assert 0.96 < proposal.take_profits[0] < proposal.take_profits[1]


def test_passive_short_places_limit_at_resistance_inner_edge():
    strategy = RangeV2PassiveStrategy()
    decision = strategy.evaluate_entry(_ctx(1.03))
    proposal = decision.proposal
    assert proposal is not None, decision.detail
    assert proposal.side is Side.SHORT
    assert proposal.entry_price == 1.04
    assert proposal.stop_price > 1.05
    assert proposal.take_profits[1] < proposal.take_profits[0] < 1.04


def test_passive_entry_skips_when_price_inside_the_zone():
    strategy = RangeV2PassiveStrategy()
    decision = strategy.evaluate_entry(_ctx(0.955))
    assert decision.proposal is None
    assert decision.reject_reason is not None


def test_passive_entry_skips_when_too_far_from_the_zone():
    strategy = RangeV2PassiveStrategy(RangeV1Config(passive_entry_distance_atr=0.5))
    decision = strategy.evaluate_entry(_ctx(0.98))
    assert decision.proposal is None
