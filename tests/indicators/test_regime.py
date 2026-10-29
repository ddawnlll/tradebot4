"""Regime classification on deterministic trend and chop series."""

from __future__ import annotations

from bot.core.indicators.regime import RangeRegimeDetector, RegimeConfig
from bot.core.models import Bar, RegimeState

T0 = 1_735_689_600_000


def series(closes: list[float]) -> list[Bar]:
    out = []
    for i, c in enumerate(closes):
        out.append(
            Bar(
                symbol="X",
                ts_open_ms=T0 + i * 3_600_000,
                ts_close_ms=T0 + (i + 1) * 3_600_000,
                open=c,
                high=c + 0.1,
                low=c - 0.1,
                close=c,
                volume=1.0,
                quote_volume=100.0,
            )
        )
    return out


def test_strong_uptrend_is_trend_up() -> None:
    det = RangeRegimeDetector(RegimeConfig(min_bars=40))
    last = None
    for b in series([100.0 + i * 0.5 for i in range(120)]):
        last = det.update(b)
    assert last is not None
    assert last.state is RegimeState.TREND_UP
    assert last.adx > 25


def test_tight_oscillation_is_range() -> None:
    det = RangeRegimeDetector(RegimeConfig(min_bars=40))
    closes = [100.0 if i % 2 == 0 else 100.4 for i in range(160)]
    last = None
    for b in series(closes):
        last = det.update(b)
    assert last is not None
    assert last.state is RegimeState.RANGE
    assert last.choppiness >= 55
