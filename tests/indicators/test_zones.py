"""Pivot confirmation delay and zone immutability."""

from __future__ import annotations

from bot.core.indicators.zones import PivotConfig, PivotDetector, ZoneConfig, ZoneDetector
from bot.core.models import Bar

T0 = 1_735_689_600_000


def bar(i: int, high: float, low: float, close: float | None = None) -> Bar:
    c = close if close is not None else (high + low) / 2
    return Bar(
        symbol="X",
        ts_open_ms=T0 + i * 60_000,
        ts_close_ms=T0 + (i + 1) * 60_000,
        open=(high + low) / 2,
        high=high,
        low=low,
        close=c,
        volume=1.0,
        quote_volume=100.0,
    )


def test_pivot_not_known_until_r_bars_later() -> None:
    det = PivotDetector(PivotConfig(left=2, right=2))
    highs = [10, 11, 15, 12, 11, 10, 9]
    seen = []
    for i, h in enumerate(highs):
        pivots = det.update(bar(i, h, h - 2))
        if pivots:
            seen.append((i, [p.price for p in pivots]))
    # The peak at index 2 can only be confirmed at index 4 (close of that bar).
    assert seen == [(4, [15])]


def test_pivot_is_immutable_and_prefix_invariant() -> None:
    highs = [10, 11, 15, 12, 11, 10, 9, 14, 9, 8]
    full = []
    det = PivotDetector(PivotConfig(left=2, right=2))
    for i, h in enumerate(highs):
        full += [(i, p) for p in det.update(bar(i, h, h - 2))]

    prefix = []
    det2 = PivotDetector(PivotConfig(left=2, right=2))
    for i, h in enumerate(highs[:7]):
        prefix += [(i, p) for p in det2.update(bar(i, h, h - 2))]

    assert prefix == [p for p in full if p[0] <= 6]
    assert all(p.confirmed_ts_ms == T0 + (i + 1) * 60_000 for i, p in full)


def test_zone_touches_accumulate_without_revision() -> None:
    det = ZoneDetector("X", ZoneConfig(atr_period=3, half_width_atr=0.2, cluster_atr=0.8, min_touches=1))
    price_path = [100, 101, 105, 101, 100, 99, 95, 99, 100, 101, 105, 101, 100]
    snapshots = []
    for i, h in enumerate(price_path):
        det.update(bar(i, h, h - 1))
        snapshots.append(det.zones())
    # Once a zone exists its touch count never decreases.
    for prev, cur in zip(snapshots, snapshots[1:], strict=False):
        for z in cur:
            match = [p for p in prev if abs(p.center - z.center) < 1e-9]
            if match:
                assert z.touches >= match[0].touches


def test_sweep_buffer_uses_only_past_breaches() -> None:
    det = ZoneDetector("X", ZoneConfig(atr_period=2, half_width_atr=0.01, cluster_atr=0.5, max_width_atr=0.1), PivotConfig(left=1, right=1))
    # Build a resistance zone at 105.
    for i, h in enumerate([100, 101, 105, 101, 100]):
        det.update(bar(i, h, h - 1))
    zones_after = det.zones("RESISTANCE", min_touches=1)
    assert zones_after
    # A wick through the zone that closes back inside records a breach.
    z = zones_after[0]
    det.update(bar(5, z.upper + 2.0, z.lower - 0.5, close=z.center))
    buf = det.sweep_buffer(0.9, fallback=0.1, min_samples=1)
    assert buf >= 1.0
