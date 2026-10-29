"""Incremental pivot and support/resistance zone detection.

No look-ahead
-------------
A pivot high at bar *i* with left window *L* and right window *R* can only be
known at the close of bar *i+R*. ``confirmed_ts_ms`` is that close time. Zones
are only updated with confirmed pivots, so at any decision time the detector
contains exactly the information a live system would have.

A pivot is *not* revised later: the pivot price and its bar timestamp are
immutable once emitted. Zone merging only adds newer confirmed pivots.
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass

from bot.core.indicators.atr import ATR
from bot.core.models import Bar, Pivot, Zone


@dataclass(slots=True)
class PivotConfig:
    left: int = 3
    right: int = 2


class PivotDetector:
    """Confirmed fractal pivots with an explicit R-bar confirmation delay."""

    def __init__(self, config: PivotConfig | None = None) -> None:
        self.config = config or PivotConfig()
        n = self.config.left + self.config.right + 1
        self._buf: deque[Bar] = deque(maxlen=n)
        self._n = 0
        self.last_confirmed_ts_ms: int | None = None

    @property
    def window(self) -> int:
        return self._buf.maxlen or 0

    def update(self, bar: Bar) -> list[Pivot]:
        self._buf.append(bar)
        self._n += 1
        if len(self._buf) < self.window:
            return []

        bars = list(self._buf)
        i = self.config.left
        candidate = bars[i]
        left_bars = bars[:i]
        right_bars = bars[i + 1 :]

        pivots: list[Pivot] = []
        if all(candidate.high > b.high for b in left_bars) and all(candidate.high > b.high for b in right_bars):
            pivots.append(
                Pivot(
                    kind="HIGH",
                    price=candidate.high,
                    bar_ts_open_ms=candidate.ts_open_ms,
                    confirmed_ts_ms=bar.ts_close_ms,
                )
            )
        if all(candidate.low < b.low for b in left_bars) and all(candidate.low < b.low for b in right_bars):
            pivots.append(
                Pivot(
                    kind="LOW",
                    price=candidate.low,
                    bar_ts_open_ms=candidate.ts_open_ms,
                    confirmed_ts_ms=bar.ts_close_ms,
                )
            )
        self.last_confirmed_ts_ms = bar.ts_close_ms
        return pivots


@dataclass(slots=True)
class ZoneConfig:
    atr_period: int = 14
    half_width_atr: float = 0.25
    cluster_atr: float = 0.6
    max_width_atr: float = 1.5
    max_age_bars: int = 800
    max_zones_per_kind: int = 12
    min_touches: int = 2
    recency_half_life_bars: float = 200.0


@dataclass(slots=True)
class _ZoneState:
    kind: str
    lower: float
    upper: float
    touches: int
    first_touch_ms: int
    last_touch_ms: int
    last_touch_bar: int
    wick_score: float = 0.0


class ZoneDetector:
    """Cluster confirmed pivots into ATR-scaled zones."""

    def __init__(self, symbol: str, config: ZoneConfig | None = None, pivot_config: PivotConfig | None = None) -> None:
        self.symbol = symbol
        self.config = config or ZoneConfig()
        self._atr = ATR(self.config.atr_period)
        self._pivots = PivotDetector(pivot_config)
        self._zones: list[_ZoneState] = []
        self._bar_index = 0
        self._last_bar: Bar | None = None
        self._breaches: deque[float] = deque(maxlen=300)

    @property
    def atr(self) -> float | None:
        return self._atr.value

    @property
    def bar_index(self) -> int:
        return self._bar_index

    def update(self, bar: Bar) -> list[Pivot]:
        """Feed a closed bar; returns pivots confirmed on this bar."""
        self._bar_index += 1
        self._last_bar = bar
        atr = self._atr.update(bar)
        pivots = self._pivots.update(bar)
        self._record_sweeps(bar)
        if atr is None or not pivots:
            return pivots

        for p in pivots:
            self._apply_pivot(p, atr)
        self._prune(bar.ts_close_ms)
        return pivots

    def _record_sweeps(self, bar: Bar) -> None:
        """Record rejection wick depth beyond an existing zone boundary.

        Only breaches that close back inside the zone count. The distribution
        of these depths is the empirical basis for stop buffers.
        """
        for z in self._zones:
            if z.kind == "RESISTANCE" and bar.high > z.upper and bar.close < z.upper:
                self._breaches.append(bar.high - z.upper)
            elif z.kind == "SUPPORT" and bar.low < z.lower and bar.close > z.lower:
                self._breaches.append(z.lower - bar.low)

    def sweep_buffer(self, quantile: float = 0.9, fallback: float = 0.0, min_samples: int = 20) -> float:
        if len(self._breaches) < min_samples:
            return fallback
        vals = sorted(self._breaches)
        idx = min(len(vals) - 1, max(0, round(quantile * (len(vals) - 1))))
        return max(vals[idx], fallback)

    def _apply_pivot(self, pivot: Pivot, atr: float) -> None:
        half = self.config.half_width_atr * atr
        tol = self.config.cluster_atr * atr
        kind = "SUPPORT" if pivot.kind == "LOW" else "RESISTANCE"

        best: _ZoneState | None = None
        best_dist = math.inf
        for z in self._zones:
            if z.kind != kind:
                continue
            dist = abs((z.lower + z.upper) / 2.0 - pivot.price)
            if dist <= tol and dist < best_dist:
                best, best_dist = z, dist

        if best is None:
            self._zones.append(
                _ZoneState(
                    kind=kind,
                    lower=pivot.price - half,
                    upper=pivot.price + half,
                    touches=1,
                    first_touch_ms=pivot.confirmed_ts_ms,
                    last_touch_ms=pivot.confirmed_ts_ms,
                    last_touch_bar=self._bar_index,
                    wick_score=0.5,
                )
            )
            return

        best.touches += 1
        best.last_touch_ms = pivot.confirmed_ts_ms
        best.last_touch_bar = self._bar_index
        best.lower = min(best.lower, pivot.price - half)
        best.upper = max(best.upper, pivot.price + half)
        max_width = self.config.max_width_atr * atr
        if best.upper - best.lower > max_width:
            center = (best.upper + best.lower) / 2.0
            best.lower = center - max_width / 2.0
            best.upper = center + max_width / 2.0
        best.wick_score = min(1.0, best.wick_score + 0.1)

    def _prune(self, now_ms: int) -> None:
        cfg = self.config
        kept: list[_ZoneState] = []
        for z in self._zones:
            age_bars = self._bar_index - z.last_touch_bar
            if age_bars > cfg.max_age_bars and z.touches < 2:
                continue
            if age_bars > cfg.max_age_bars * 2:
                continue
            kept.append(z)
        # Keep the strongest zones per kind.
        for kind in ("SUPPORT", "RESISTANCE"):
            group = [z for z in kept if z.kind == kind]
            if len(group) > cfg.max_zones_per_kind:
                group.sort(key=lambda z: self._strength(z), reverse=True)
                keep_ids = {id(z) for z in group[: cfg.max_zones_per_kind]}
                kept = [z for z in kept if z.kind != kind or id(z) in keep_ids]
        self._zones = kept

    def _strength(self, z: _ZoneState) -> float:
        age = max(0, self._bar_index - z.last_touch_bar)
        recency = 0.5 ** (age / self.config.recency_half_life_bars)
        return (1.0 + math.log1p(z.touches)) * recency * (0.5 + z.wick_score)

    def zones(self, kind: str | None = None, min_touches: int | None = None) -> list[Zone]:
        mt = self.config.min_touches if min_touches is None else min_touches
        out: list[Zone] = []
        for z in self._zones:
            if kind is not None and z.kind != kind:
                continue
            if z.touches < mt:
                continue
            out.append(
                Zone(
                    kind=z.kind,  # type: ignore[arg-type]
                    lower=z.lower,
                    upper=z.upper,
                    touches=z.touches,
                    first_touch_ms=z.first_touch_ms,
                    last_touch_ms=z.last_touch_ms,
                    strength=self._strength(z),
                )
            )
        out.sort(key=lambda z: z.center)
        return out

    def best_support_below(self, price: float, min_touches: int | None = None) -> Zone | None:
        cands = [z for z in self.zones("SUPPORT", min_touches) if z.center < price]
        if not cands:
            return None
        return max(cands, key=lambda z: (z.touches, z.strength, z.center))

    def best_resistance_above(self, price: float, min_touches: int | None = None) -> Zone | None:
        cands = [z for z in self.zones("RESISTANCE", min_touches) if z.center > price]
        if not cands:
            return None
        return max(cands, key=lambda z: (z.touches, z.strength, -z.center))

    def recent_overlap(self, start_ms: int, end_ms: int) -> list[Zone]:
        return [z for z in self.zones() if z.last_touch_ms >= start_ms and z.first_touch_ms <= end_ms]

    def snapshot(self) -> dict:
        return {
            "bar_index": self._bar_index,
            "atr": self._atr.value,
            "zones": [
                {
                    "kind": z.kind,
                    "lower": z.lower,
                    "upper": z.upper,
                    "touches": z.touches,
                    "strength": self._strength(z),
                }
                for z in self._zones
            ],
        }
