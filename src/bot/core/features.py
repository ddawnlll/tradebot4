"""Versioned, point-in-time MarketState feature snapshot.

This module is observational. Nothing here is wired into a strategy, the risk
path or the decision engine, so importing it cannot change a default decision.

Point-in-time contract
----------------------
``FeatureState`` is fed *closed* bars in strictly increasing open time (the
:class:`~bot.core.models.Bar` contract already guarantees ``ts_close_ms >
ts_open_ms``). A snapshot computed after bar ``t`` uses only bars ``<= t``:

* every ``FeatureValue.available_time_ms`` is the close of bar ``t``;
* every ``FeatureValue.input_ts_ms`` is the open of bar ``t``;
* pivots are not emitted until the full right window has closed, and a pivot
  is never revised after emission (``confirmed_ts_ms`` records the close at
  which it passed confirmation);
* there is no wall clock and no I/O, so replaying the same bars always yields
  the same snapshot.

Fidelity to the v8-core catalogue
---------------------------------
Formulas follow ``v8-core/src/state.rs`` where the catalogue is authoritative:
``real_body`` is absolute, candle ratios fall back to ``0.0``/``0.5`` on a zero
range, gap size is measured against the *previous bar's high/low*, window
extremes cover the ``n`` bars **before** the current bar, ATR is Wilder(14),
Bollinger is SMA(20) +/- 2 population sigma, ``vol_zscore`` is a 100-bar
population z-score, CMF is the classic 20-bar Chaikin Money Flow, and swing
significance requires the pivot bar's own range to be at least ``1.0 * ATR``.
Differences are deliberate and documented: warmup is an explicit ``None`` with
``NOT_YET_AVAILABLE`` instead of a sentinel, and a fallback value carries
``quality=DEGRADED`` instead of silently pretending to be complete.

Feature count is intentionally small (36 values in five groups). Adding more
columns is a separate, preregistered batch: every new feature is another
multiple-testing draw.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections import deque
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Any

from bot.core.indicators.atr import ATR
from bot.core.models import HOUR_MS, Bar

FEATURE_VERSION = "features_v1"
FEATURE_GRAPH_NAME = "tradebot4.market_state.features.v1"

CANDLE_FEATURES: tuple[str, ...] = (
    "real_body",
    "body_range_ratio",
    "upper_shadow",
    "lower_shadow",
    "close_position",
    "inside_bar",
    "outside_bar",
    "gap_size",
    "gap_dir",
)

VOLATILITY_FEATURES: tuple[str, ...] = (
    "atr_14",
    "bb_mid",
    "bb_upper",
    "bb_lower",
    "bb_pct_b",
    "bb_bandwidth",
    "atr_2sigma_active",
    "atr_trend_phase",
)

LOCATION_FEATURES: tuple[str, ...] = (
    "prior_high",
    "prior_low",
    "window_high_10",
    "window_low_10",
    "window_high_20",
    "window_low_20",
    "window_high_50",
    "window_low_50",
    "range_height_10",
    "range_height_20",
    "range_height_50",
    "swing_high_10",
    "swing_low_10",
    "swing_high_20",
    "swing_low_20",
)

PARTICIPATION_FEATURES: tuple[str, ...] = (
    "vol_zscore_100",
    "cmf_20",
)

SESSION_FEATURES: tuple[str, ...] = (
    "hour_of_day_utc",
    "impulsive_window",
)

FEATURE_GROUPS: Mapping[str, tuple[str, ...]] = MappingProxyType(
    {
        "candle_shape": CANDLE_FEATURES,
        "volatility": VOLATILITY_FEATURES,
        "location": LOCATION_FEATURES,
        "participation": PARTICIPATION_FEATURES,
        "session": SESSION_FEATURES,
    }
)
FEATURE_NAMES: tuple[str, ...] = tuple(name for names in FEATURE_GROUPS.values() for name in names)


class FeatureQuality(StrEnum):
    """Whether a value may be used as-is.

    ``COMPLETE``  computed from sufficient, contiguous input.
    ``DEGRADED``  a documented fallback was used (zero range/variance/volume)
                  or the input window contains a bar gap. The number is still
                  defined, but research code should treat it as provisional.
    ``INVALID``   ``value`` is ``None``; ``null_reason`` says why.
    """

    COMPLETE = "COMPLETE"
    DEGRADED = "DEGRADED"
    INVALID = "INVALID"


class NullReason(StrEnum):
    """Why a feature is ``None``.

    ``NOT_YET_AVAILABLE``    the window/indicator has not warmed up.
    ``NO_SIGNIFICANT_PIVOT`` warmup is over but no confirmed pivot meets the
                             ``range >= 1.0 * ATR`` significance test.
    """

    NOT_YET_AVAILABLE = "NOT_YET_AVAILABLE"
    NO_SIGNIFICANT_PIVOT = "NO_SIGNIFICANT_PIVOT"


@dataclass(frozen=True, slots=True)
class FeatureConfig:
    """Window and version parameters that fully define the feature graph."""

    atr_period: int = 14
    bb_period: int = 20
    bb_std: float = 2.0
    atr_band_period: int = 20
    atr_band_std: float = 2.0
    atr_trend_lag: int = 10
    vol_zscore_period: int = 100
    cmf_period: int = 20
    window_sizes: tuple[int, ...] = (10, 20, 50)
    swing_windows: tuple[int, ...] = (10, 20)
    swing_significance_atr: float = 1.0
    history: int = 600
    impulsive_start_hour: int = 8
    impulsive_end_hour: int = 17

    def __post_init__(self) -> None:
        object.__setattr__(self, "window_sizes", tuple(self.window_sizes))
        object.__setattr__(self, "swing_windows", tuple(self.swing_windows))
        for label, value in (
            ("atr_period", self.atr_period),
            ("bb_period", self.bb_period),
            ("atr_band_period", self.atr_band_period),
            ("atr_trend_lag", self.atr_trend_lag),
            ("vol_zscore_period", self.vol_zscore_period),
            ("cmf_period", self.cmf_period),
            ("history", self.history),
        ):
            if value < 1:
                raise ValueError(f"{label} must be >= 1")
        if self.bb_period < 2:
            raise ValueError("bb_period must be >= 2")
        if self.bb_std <= 0:
            raise ValueError("bb_std must be > 0")
        if self.atr_band_std <= 0:
            raise ValueError("atr_band_std must be > 0")
        if self.swing_significance_atr < 0:
            raise ValueError("swing_significance_atr must be >= 0")
        if not self.window_sizes or any(n < 2 for n in self.window_sizes):
            raise ValueError("window_sizes must be a non-empty tuple of n >= 2")
        if not self.swing_windows or any(n < 1 for n in self.swing_windows):
            raise ValueError("swing_windows must be a non-empty tuple of n >= 1")
        if any(b <= a for a, b in zip(self.window_sizes, self.window_sizes[1:], strict=False)):
            raise ValueError("window_sizes must be strictly increasing")
        if any(b <= a for a, b in zip(self.swing_windows, self.swing_windows[1:], strict=False)):
            raise ValueError("swing_windows must be strictly increasing")
        if max(self.swing_windows) * 2 + 1 > self.history:
            raise ValueError("history must cover the largest swing window")
        if max(self.window_sizes) + 1 > self.history:
            raise ValueError("history must cover the largest window")
        if max(
            self.vol_zscore_period,
            self.cmf_period,
            self.bb_period,
            self.atr_band_period,
            self.atr_trend_lag + 1,
        ) > self.history:
            raise ValueError("history must cover every indicator window")
        if not 0 <= self.impulsive_start_hour <= self.impulsive_end_hour <= 23:
            raise ValueError("impulsive hours must satisfy 0 <= start <= end <= 23")

    def as_dict(self) -> dict[str, Any]:
        return {
            "atr_period": self.atr_period,
            "bb_period": self.bb_period,
            "bb_std": self.bb_std,
            "atr_band_period": self.atr_band_period,
            "atr_band_std": self.atr_band_std,
            "atr_trend_lag": self.atr_trend_lag,
            "vol_zscore_period": self.vol_zscore_period,
            "cmf_period": self.cmf_period,
            "window_sizes": list(self.window_sizes),
            "swing_windows": list(self.swing_windows),
            "swing_significance_atr": self.swing_significance_atr,
            "history": self.history,
            "impulsive_start_hour": self.impulsive_start_hour,
            "impulsive_end_hour": self.impulsive_end_hour,
        }


def feature_graph_version(config: FeatureConfig | None = None) -> str:
    """Stable 16-hex hash of the batch definition and all window parameters."""
    cfg = config or FeatureConfig()
    payload = {
        "batch": FEATURE_GRAPH_NAME,
        "feature_version": FEATURE_VERSION,
        "groups": {group: list(names) for group, names in FEATURE_GROUPS.items()},
        "config": cfg.as_dict(),
    }
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


FEATURE_GRAPH_VERSION = feature_graph_version()


@dataclass(frozen=True, slots=True)
class FeatureValue:
    """One named value with its point-in-time and provenance metadata."""

    name: str
    value: float | bool | None
    available_time_ms: int
    input_ts_ms: int
    feature_version: str
    quality: FeatureQuality
    null_reason: NullReason | None = None
    confirmed_ts_ms: int | None = None

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("feature name must be non-empty")
        if self.value is None and self.quality is not FeatureQuality.INVALID:
            raise ValueError(f"{self.name}: a None value must have quality INVALID")
        if self.value is not None and self.quality is FeatureQuality.INVALID:
            raise ValueError(f"{self.name}: an INVALID value must be None")
        if self.value is None and self.null_reason is None:
            raise ValueError(f"{self.name}: a None value must carry a null_reason")
        if self.value is not None and self.null_reason is not None:
            raise ValueError(f"{self.name}: a present value must not carry a null_reason")

    @property
    def is_available(self) -> bool:
        return self.value is not None

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "value": self.value,
            "available_time_ms": self.available_time_ms,
            "input_ts_ms": self.input_ts_ms,
            "feature_version": self.feature_version,
            "quality": self.quality.value,
            "null_reason": self.null_reason.value if self.null_reason is not None else None,
            "confirmed_ts_ms": self.confirmed_ts_ms,
        }


@dataclass(frozen=True, slots=True)
class FeatureSnapshot:
    """All feature values known at the close of one bar."""

    symbol: str
    ts_open_ms: int
    ts_close_ms: int
    feature_version: str
    graph_version: str
    values: Mapping[str, FeatureValue]

    def __post_init__(self) -> None:
        if self.ts_close_ms <= self.ts_open_ms:
            raise ValueError("snapshot close must be after open")
        object.__setattr__(self, "values", MappingProxyType(dict(self.values)))
        missing = set(FEATURE_NAMES).difference(self.values)
        if missing:
            raise ValueError(f"snapshot missing features: {sorted(missing)}")
        unknown = set(self.values).difference(FEATURE_NAMES)
        if unknown:
            raise ValueError(f"snapshot contains unknown features: {sorted(unknown)}")

    @property
    def available_time_ms(self) -> int:
        return self.ts_close_ms

    def __len__(self) -> int:
        return len(self.values)

    def __getitem__(self, name: str) -> FeatureValue:
        return self.values[name]

    def get(self, name: str) -> FeatureValue:
        return self.values[name]

    def value(self, name: str) -> float | bool | None:
        return self.values[name].value

    def available(self, name: str) -> bool:
        return self.values[name].is_available

    def values_only(self) -> dict[str, float | bool | None]:
        """Flat ``name -> value`` view for research tables (``None`` preserved)."""
        return {name: fv.value for name, fv in self.values.items()}

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "ts_open_ms": self.ts_open_ms,
            "ts_close_ms": self.ts_close_ms,
            "available_time_ms": self.available_time_ms,
            "feature_version": self.feature_version,
            "graph_version": self.graph_version,
            "features": {name: fv.to_dict() for name, fv in self.values.items()},
        }


@dataclass(frozen=True, slots=True)
class _PivotRecord:
    """A confirmed fractal pivot, frozen at confirmation time."""

    bar_index: int
    price: float
    bar_range: float
    confirm_ts_ms: int
    had_gap: bool


def _sign(value: float) -> float:
    if value > 0:
        return 1.0
    if value < 0:
        return -1.0
    return 0.0


def _mean_std(values: Sequence[float]) -> tuple[float, float]:
    """Population mean and standard deviation (0 std when the window is flat)."""
    count = len(values)
    mean = math.fsum(values) / count
    variance = math.fsum((v - mean) ** 2 for v in values) / count
    return mean, math.sqrt(max(0.0, variance))


class FeatureState:
    """Incremental per-symbol feature engine.

    Feed exactly one closed bar per :meth:`update` call, in increasing open
    time. The engine keeps a bounded bar buffer for rolling windows and a
    bounded pivot store; the running prefix extremes are unbounded, so
    ``prior_high``/``prior_low`` remain true prefix extremes even after the
    bar buffer rolls.
    """

    def __init__(self, symbol: str, config: FeatureConfig | None = None) -> None:
        if not symbol:
            raise ValueError("symbol must be non-empty")
        self.symbol = symbol
        self.config = config or FeatureConfig()
        self.graph_version = feature_graph_version(self.config)

        self._bars: deque[Bar] = deque(maxlen=self.config.history)
        self._gap_flags: deque[bool] = deque(maxlen=self.config.history)
        self._atr = ATR(self.config.atr_period)
        self._atr_series: deque[float] = deque(
            maxlen=max(self.config.atr_band_period, self.config.atr_trend_lag + 1)
        )
        self._pivots_high: dict[int, deque[_PivotRecord]] = {
            n: deque(maxlen=128) for n in self.config.swing_windows
        }
        self._pivots_low: dict[int, deque[_PivotRecord]] = {
            n: deque(maxlen=128) for n in self.config.swing_windows
        }

        self._bar_index = -1
        self._last_bar: Bar | None = None
        self._any_gap = False
        self._prefix_high: float | None = None
        self._prefix_low: float | None = None
        self._snapshot: FeatureSnapshot | None = None

    # ------------------------------------------------------------------ feeds
    @property
    def last_snapshot(self) -> FeatureSnapshot | None:
        return self._snapshot

    @property
    def bar_count(self) -> int:
        return self._bar_index + 1

    def update(self, bar: Bar) -> FeatureSnapshot:
        self._accept(bar)
        bars = list(self._bars)
        atr_value = self._atr.update(bar)
        if atr_value is not None:
            self._atr_series.append(atr_value)
        self._confirm_pivots(bars, bar)

        snapshot = self._snapshot_from(bar, bars)
        self._snapshot = snapshot
        self._prefix_high = bar.high if self._prefix_high is None else max(self._prefix_high, bar.high)
        self._prefix_low = bar.low if self._prefix_low is None else min(self._prefix_low, bar.low)
        return snapshot

    def reset(self) -> None:
        """Return to the just-constructed state. The config is unchanged."""
        self._bars.clear()
        self._gap_flags.clear()
        self._atr.reset()
        self._atr_series.clear()
        for store in (*self._pivots_high.values(), *self._pivots_low.values()):
            store.clear()
        self._bar_index = -1
        self._last_bar = None
        self._any_gap = False
        self._prefix_high = None
        self._prefix_low = None
        self._snapshot = None

    def _accept(self, bar: Bar) -> None:
        if bar.symbol != self.symbol:
            raise ValueError(f"expected {self.symbol}, got {bar.symbol}")
        if self._last_bar is not None and bar.ts_open_ms <= self._last_bar.ts_open_ms:
            raise ValueError(
                f"bars must arrive once in increasing open time: "
                f"{bar.ts_open_ms} after {self._last_bar.ts_open_ms}"
            )
        gap = self._last_bar is not None and bar.ts_open_ms != self._last_bar.ts_close_ms
        self._last_bar = bar
        self._bar_index += 1
        self._bars.append(bar)
        self._gap_flags.append(gap)
        self._any_gap = self._any_gap or gap

    # --------------------------------------------------------------- pivots
    def _confirm_pivots(self, bars: list[Bar], bar: Bar) -> None:
        """Detect the pivot that just received its final right-window bar.

        With ``n`` bars on each side, the pivot candidate is ``n`` bars back,
        so it is only written when ``2n + 1`` bars exist. No later bar can
        retract it.
        """
        index = self._bar_index
        for n in self.config.swing_windows:
            if len(bars) < 2 * n + 1:
                continue
            candidate = bars[-(n + 1)]
            left = bars[-(2 * n + 1) : -(n + 1)]
            right = bars[-n:]
            had_gap = self._window_has_gap(2 * n + 1)
            pivot_index = index - n
            if all(candidate.high > other.high for other in left) and all(
                candidate.high > other.high for other in right
            ):
                self._pivots_high[n].append(
                    _PivotRecord(
                        bar_index=pivot_index,
                        price=candidate.high,
                        bar_range=candidate.high - candidate.low,
                        confirm_ts_ms=bar.ts_close_ms,
                        had_gap=had_gap,
                    )
                )
            if all(candidate.low < other.low for other in left) and all(
                candidate.low < other.low for other in right
            ):
                self._pivots_low[n].append(
                    _PivotRecord(
                        bar_index=pivot_index,
                        price=candidate.low,
                        bar_range=candidate.high - candidate.low,
                        confirm_ts_ms=bar.ts_close_ms,
                        had_gap=had_gap,
                    )
                )
        self._prune_pivots(index)

    def _prune_pivots(self, index: int) -> None:
        cutoff = index - self.config.history
        for store in (*self._pivots_high.values(), *self._pivots_low.values()):
            while store and store[0].bar_index < cutoff:
                store.popleft()

    # -------------------------------------------------------------- snapshot
    def _snapshot_from(self, bar: Bar, bars: list[Bar]) -> FeatureSnapshot:
        values: dict[str, FeatureValue] = {}

        def add(
            name: str,
            value: float | bool | None,
            *,
            quality: FeatureQuality = FeatureQuality.COMPLETE,
            null_reason: NullReason | None = None,
            confirmed_ts_ms: int | None = None,
        ) -> None:
            values[name] = FeatureValue(
                name=name,
                value=value,
                available_time_ms=bar.ts_close_ms,
                input_ts_ms=bar.ts_open_ms,
                feature_version=FEATURE_VERSION,
                quality=quality,
                null_reason=null_reason,
                confirmed_ts_ms=confirmed_ts_ms,
            )

        def null(name: str, reason: NullReason) -> None:
            add(name, None, quality=FeatureQuality.INVALID, null_reason=reason)

        self._candle_values(bar, bars, add, null)
        self._volatility_values(bars, add, null)
        self._location_values(bars, add, null)
        self._participation_values(bars, add, null)
        self._session_values(bar, add)

        return FeatureSnapshot(
            symbol=self.symbol,
            ts_open_ms=bar.ts_open_ms,
            ts_close_ms=bar.ts_close_ms,
            feature_version=FEATURE_VERSION,
            graph_version=self.graph_version,
            values=values,
        )

    def _candle_values(self, bar: Bar, bars: list[Bar], add: Any, null: Any) -> None:
        bar_range = bar.high - bar.low
        real_body = abs(bar.close - bar.open)
        add("real_body", real_body)
        if bar_range > 0:
            add("body_range_ratio", real_body / bar_range)
            add("close_position", (bar.close - bar.low) / bar_range)
        else:
            # v8 fallback for a zero-range bar: no division, explicit degradation.
            add("body_range_ratio", 0.0, quality=FeatureQuality.DEGRADED)
            add("close_position", 0.5, quality=FeatureQuality.DEGRADED)
        add("upper_shadow", bar.high - max(bar.open, bar.close))
        add("lower_shadow", min(bar.open, bar.close) - bar.low)

        if len(bars) < 2:
            for name in ("inside_bar", "outside_bar", "gap_size", "gap_dir"):
                null(name, NullReason.NOT_YET_AVAILABLE)
            return

        previous = bars[-2]
        add("inside_bar", bar.high <= previous.high and bar.low >= previous.low)
        add("outside_bar", bar.high >= previous.high and bar.low <= previous.low)
        if bar.open > previous.high:
            add("gap_size", bar.open - previous.high)
            add("gap_dir", 1.0)
        elif bar.open < previous.low:
            add("gap_size", bar.open - previous.low)
            add("gap_dir", -1.0)
        else:
            add("gap_size", 0.0)
            add("gap_dir", 0.0)

    def _volatility_values(self, bars: list[Bar], add: Any, null: Any) -> None:
        cfg = self.config
        atr_value = self._atr.value
        if atr_value is None:
            null("atr_14", NullReason.NOT_YET_AVAILABLE)
        else:
            add("atr_14", atr_value, quality=FeatureQuality.DEGRADED if self._any_gap else FeatureQuality.COMPLETE)

        if len(bars) < cfg.bb_period:
            for name in ("bb_mid", "bb_upper", "bb_lower", "bb_pct_b", "bb_bandwidth"):
                null(name, NullReason.NOT_YET_AVAILABLE)
        else:
            window = [b.close for b in bars[-cfg.bb_period :]]
            mid, std = _mean_std(window)
            upper = mid + cfg.bb_std * std
            lower = mid - cfg.bb_std * std
            degraded = self._window_has_gap(cfg.bb_period)
            quality = FeatureQuality.DEGRADED if degraded else FeatureQuality.COMPLETE
            add("bb_mid", mid, quality=quality)
            add("bb_upper", upper, quality=quality)
            add("bb_lower", lower, quality=quality)
            if upper > lower:
                add("bb_pct_b", (bars[-1].close - lower) / (upper - lower), quality=quality)
            else:
                add("bb_pct_b", 0.5, quality=FeatureQuality.DEGRADED)
            if mid != 0.0:
                add("bb_bandwidth", (upper - lower) / mid, quality=quality)
            else:
                add("bb_bandwidth", 0.0, quality=FeatureQuality.DEGRADED)

        if len(self._atr_series) < cfg.atr_band_period:
            null("atr_2sigma_active", NullReason.NOT_YET_AVAILABLE)
        else:
            window = list(self._atr_series)[-cfg.atr_band_period :]
            mean, std = _mean_std(window)
            band = mean + cfg.atr_band_std * std
            add(
                "atr_2sigma_active",
                self._atr_series[-1] > band,
                quality=FeatureQuality.DEGRADED if self._any_gap else FeatureQuality.COMPLETE,
            )

        if len(self._atr_series) < cfg.atr_trend_lag + 1:
            null("atr_trend_phase", NullReason.NOT_YET_AVAILABLE)
        else:
            delta = self._atr_series[-1] - self._atr_series[-1 - cfg.atr_trend_lag]
            add(
                "atr_trend_phase",
                _sign(delta),
                quality=FeatureQuality.DEGRADED if self._any_gap else FeatureQuality.COMPLETE,
            )

    def _location_values(self, bars: list[Bar], add: Any, null: Any) -> None:
        cfg = self.config
        prior_quality = FeatureQuality.DEGRADED if self._any_gap else FeatureQuality.COMPLETE
        if self._prefix_high is None or self._prefix_low is None:
            null("prior_high", NullReason.NOT_YET_AVAILABLE)
            null("prior_low", NullReason.NOT_YET_AVAILABLE)
        else:
            add("prior_high", self._prefix_high, quality=prior_quality)
            add("prior_low", self._prefix_low, quality=prior_quality)

        for n in cfg.window_sizes:
            if len(bars) < n + 1:
                null(f"window_high_{n}", NullReason.NOT_YET_AVAILABLE)
                null(f"window_low_{n}", NullReason.NOT_YET_AVAILABLE)
                null(f"range_height_{n}", NullReason.NOT_YET_AVAILABLE)
                continue
            window = bars[-(n + 1) : -1]
            high = max(b.high for b in window)
            low = min(b.low for b in window)
            quality = (
                FeatureQuality.DEGRADED
                if self._prior_window_has_gap(n)
                else FeatureQuality.COMPLETE
            )
            add(f"window_high_{n}", high, quality=quality)
            add(f"window_low_{n}", low, quality=quality)
            add(f"range_height_{n}", high - low, quality=quality)

        atr_value = self._atr.value
        for n in cfg.swing_windows:
            for kind, store in (("swing_high", self._pivots_high[n]), ("swing_low", self._pivots_low[n])):
                name = f"{kind}_{n}"
                if atr_value is None:
                    null(name, NullReason.NOT_YET_AVAILABLE)
                    continue
                if not store:
                    reason = (
                        NullReason.NOT_YET_AVAILABLE
                        if len(bars) < 2 * n + 1
                        else NullReason.NO_SIGNIFICANT_PIVOT
                    )
                    null(name, reason)
                    continue
                threshold = cfg.swing_significance_atr * atr_value
                pivot = next((p for p in reversed(store) if p.bar_range >= threshold), None)
                if pivot is None:
                    null(name, NullReason.NO_SIGNIFICANT_PIVOT)
                else:
                    add(
                        name,
                        pivot.price,
                        quality=FeatureQuality.DEGRADED if pivot.had_gap else FeatureQuality.COMPLETE,
                        confirmed_ts_ms=pivot.confirm_ts_ms,
                    )

    def _participation_values(self, bars: list[Bar], add: Any, null: Any) -> None:
        cfg = self.config
        if len(bars) < cfg.vol_zscore_period:
            null("vol_zscore_100", NullReason.NOT_YET_AVAILABLE)
        else:
            window = [b.volume for b in bars[-cfg.vol_zscore_period :]]
            mean, std = _mean_std(window)
            degraded = self._window_has_gap(cfg.vol_zscore_period)
            if std > 0.0:
                add(
                    "vol_zscore_100",
                    (bars[-1].volume - mean) / std,
                    quality=FeatureQuality.DEGRADED if degraded else FeatureQuality.COMPLETE,
                )
            else:
                add("vol_zscore_100", 0.0, quality=FeatureQuality.DEGRADED)

        if len(bars) < cfg.cmf_period:
            null("cmf_20", NullReason.NOT_YET_AVAILABLE)
        else:
            money_flow = 0.0
            volume_sum = 0.0
            for b in bars[-cfg.cmf_period :]:
                bar_range = b.high - b.low
                if bar_range > 0.0:
                    money_flow += ((b.close - b.low) - (b.high - b.close)) / bar_range * b.volume
                volume_sum += b.volume
            degraded = self._window_has_gap(cfg.cmf_period)
            if volume_sum <= 0.0:
                add("cmf_20", 0.0, quality=FeatureQuality.DEGRADED)
            else:
                add(
                    "cmf_20",
                    money_flow / volume_sum,
                    quality=FeatureQuality.DEGRADED if degraded else FeatureQuality.COMPLETE,
                )

    def _session_values(self, bar: Bar, add: Any) -> None:
        # The decision happens at the bar close, so the session label uses that
        # instant (matching v8's ``event_time``).
        hour = (bar.ts_close_ms // HOUR_MS) % 24
        add("hour_of_day_utc", float(hour))
        add(
            "impulsive_window",
            self.config.impulsive_start_hour <= hour <= self.config.impulsive_end_hour,
        )

    # ----------------------------------------------------------------- helpers
    def _window_has_gap(self, size: int) -> bool:
        """True when the last ``size`` bars (including the current) have a gap."""
        if size < 2 or len(self._gap_flags) < size:
            return False
        flags = list(self._gap_flags)
        return any(flags[-size + 1 :])

    def _prior_window_has_gap(self, size: int) -> bool:
        """True when the ``size`` bars before the current bar have an internal gap."""
        if size < 2 or len(self._gap_flags) < size + 1:
            return False
        flags = list(self._gap_flags)
        return any(flags[-size:-1])


def compute_snapshot(
    symbol: str, bars: Iterable[Bar], config: FeatureConfig | None = None
) -> FeatureSnapshot | None:
    """Replay ``bars`` through a fresh engine and return the final snapshot.

    This is a convenience for research/ablation scripts. It has no strategy
    side effects; an empty iterable returns ``None``.
    """
    state = FeatureState(symbol, config)
    snapshot: FeatureSnapshot | None = None
    for bar in bars:
        snapshot = state.update(bar)
    return snapshot


def compute_snapshots(
    symbol: str, bars: Iterable[Bar], config: FeatureConfig | None = None
) -> list[FeatureSnapshot]:
    """Replay ``bars`` and return every snapshot (one per bar)."""
    state = FeatureState(symbol, config)
    return [state.update(bar) for bar in bars]
