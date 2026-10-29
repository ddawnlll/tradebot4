"""Aggregation invariants: alignment, completeness, prefix invariance."""

from __future__ import annotations

from bot.core.aggregation import BarAggregator
from bot.core.models import Bar

T0 = 1_735_689_600_000  # 2025-01-01T00:00Z, aligned


def mk(i: int, px: float = 100.0, vol: float = 1.0, tf: int = 60_000) -> Bar:
    return Bar(
        symbol="X",
        ts_open_ms=T0 + i * tf,
        ts_close_ms=T0 + (i + 1) * tf,
        open=px,
        high=px + 1,
        low=px - 1,
        close=px + 0.5,
        volume=vol,
        quote_volume=vol * px,
    )


def test_5m_bucket_closes_at_boundary() -> None:
    agg = BarAggregator("X", 5 * 60_000)
    out = None
    for i in range(5):
        out = agg.update(mk(i, 100 + i))
        if i < 4:
            assert out is None
    assert out is not None
    assert out.complete
    assert out.bar.ts_open_ms == T0
    assert out.bar.ts_close_ms == T0 + 5 * 60_000
    assert out.bar.open == 100
    assert out.bar.close == 104.5
    assert out.bar.high == 105
    assert out.bar.low == 99
    assert out.bar.volume == 5
    assert out.actual_bars == 5


def test_gap_marks_bucket_incomplete() -> None:
    agg = BarAggregator("X", 5 * 60_000)
    emitted = []
    for i in [0, 1, 3, 4]:
        r = agg.update(mk(i))
        if r is not None:
            emitted.append(r)
    assert len(emitted) == 1
    assert not emitted[0].complete
    assert emitted[0].actual_bars == 4
    assert agg.gaps == 1


def test_out_of_order_bar_is_ignored() -> None:
    agg = BarAggregator("X", 5 * 60_000)
    agg.update(mk(0))
    agg.update(mk(1))
    agg.update(mk(0))
    assert agg.out_of_order + agg.duplicates == 1
    assert agg._count == 2  # type: ignore[attr-defined]


def test_prefix_invariance() -> None:
    def run(n: int):
        agg = BarAggregator("X", 5 * 60_000)
        out = []
        for i in range(n):
            r = agg.update(mk(i, 100 + (i % 3)))
            if r is not None:
                out.append((r.bar.ts_open_ms, r.bar.open, r.bar.high, r.bar.low, r.bar.close, r.bar.volume, r.complete))
        return out

    full = run(60)
    prefix = run(45)
    assert prefix == [p for p in full if p[0] < T0 + 45 * 60_000]


def test_misaligned_input_never_reports_complete() -> None:
    """Safe failure mode: bars that do not align to the timeframe grid are not 'complete'."""
    agg = BarAggregator("X", 5 * 60_000)
    offset = 20_000
    out = None
    for i in range(10):
        b = mk(i)
        b = Bar(b.symbol, b.ts_open_ms + offset, b.ts_close_ms + offset, b.open, b.high, b.low, b.close, b.volume, b.quote_volume)
        out = agg.update(b)
    assert out is None or not out.complete
