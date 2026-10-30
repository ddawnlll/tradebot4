"""Tests for the observational MarketState feature batch.

The assertions deliberately recompute several features independently from the
raw bar list, so the incremental engine is checked against a batch definition
rather than against itself.
"""

from __future__ import annotations

import json
import math
import random

import pytest

from bot.core.features import (
    FEATURE_GRAPH_NAME,
    FEATURE_NAMES,
    FEATURE_VERSION,
    FeatureConfig,
    FeatureQuality,
    FeatureState,
    NullReason,
    compute_snapshot,
    compute_snapshots,
    feature_graph_version,
)
from bot.core.models import MINUTE_MS, Bar

SYMBOL = "TESTUSDT"
T0 = 1_704_067_200_000  # 2024-01-01T00:00:00Z, aligned to 1m and 1h


def make_bar(
    index: int,
    open_: float,
    high: float,
    low: float,
    close: float,
    volume: float = 1.0,
    *,
    symbol: str = SYMBOL,
    start_ms: int = T0,
    tf_ms: int = MINUTE_MS,
) -> Bar:
    ts_open = start_ms + index * tf_ms
    return Bar(
        symbol=symbol,
        ts_open_ms=ts_open,
        ts_close_ms=ts_open + tf_ms,
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=volume,
        quote_volume=close * volume,
    )


def flat_series(
    count: int,
    *,
    price: float = 100.0,
    spread: float = 1.0,
    volume: float = 1.0,
    start_ms: int = T0,
) -> list[Bar]:
    half = spread / 2.0
    return [
        make_bar(i, price, price + half, price - half, price, volume, start_ms=start_ms) for i in range(count)
    ]


def synthetic_bars(count: int, *, seed: int = 7, start_ms: int = T0) -> list[Bar]:
    """Deterministic, strictly positive, contiguous OHLCV series."""
    rng = random.Random(seed)
    price = 100.0
    bars: list[Bar] = []
    for i in range(count):
        open_ = price
        close = max(1.0, price + 0.4 * math.sin(i / 7.0) + rng.uniform(-0.2, 0.2))
        high = max(open_, close) + rng.uniform(0.05, 0.6)
        low = min(open_, close) - rng.uniform(0.05, 0.6)
        volume = 5.0 + 3.0 * abs(math.sin(i / 5.0)) + rng.uniform(0.0, 1.0)
        bars.append(make_bar(i, open_, high, low, close, volume, start_ms=start_ms))
        price = close
    return bars


def manual_atr_series(bars: list[Bar], period: int = 14) -> list[float | None]:
    """Independent Wilder ATR series aligned with ``bars``."""
    out: list[float | None] = []
    values: list[float] = []
    current: float | None = None
    previous_close: float | None = None
    for bar in bars:
        if previous_close is None:
            tr = bar.high - bar.low
        else:
            tr = max(bar.high - bar.low, abs(bar.high - previous_close), abs(bar.low - previous_close))
        previous_close = bar.close
        if current is None:
            values.append(tr)
            if len(values) == period:
                current = sum(values) / period
        else:
            current = (current * (period - 1) + tr) / period
        out.append(current)
    return out


def mean_std(values: list[float]) -> tuple[float, float]:
    mean = math.fsum(values) / len(values)
    variance = math.fsum((v - mean) ** 2 for v in values) / len(values)
    return mean, math.sqrt(max(0.0, variance))


# ---------------------------------------------------------------------------
# API shape and provenance
# ---------------------------------------------------------------------------


def test_feature_manifest_is_small_and_complete():
    assert len(FEATURE_NAMES) == 36
    assert len(set(FEATURE_NAMES)) == 36
    assert feature_graph_version() == feature_graph_version(FeatureConfig())
    assert len(feature_graph_version()) == 16
    assert int(feature_graph_version(), 16) >= 0
    assert feature_graph_version() != feature_graph_version(FeatureConfig(bb_period=21))


def test_snapshot_carries_full_provenance_and_is_json_serializable():
    bars = synthetic_bars(40)
    state = FeatureState(SYMBOL)
    snapshot = state.update(bars[-1])

    assert snapshot.symbol == SYMBOL
    assert snapshot.ts_open_ms == bars[-1].ts_open_ms
    assert snapshot.ts_close_ms == bars[-1].ts_close_ms
    assert snapshot.available_time_ms == bars[-1].ts_close_ms
    assert snapshot.feature_version == FEATURE_VERSION
    assert snapshot.graph_version == feature_graph_version()
    assert snapshot.graph_version == state.graph_version
    assert set(snapshot.values) == set(FEATURE_NAMES)

    for name, value in snapshot.values.items():
        assert value.name == name
        assert value.input_ts_ms == bars[-1].ts_open_ms
        assert value.available_time_ms == bars[-1].ts_close_ms
        assert value.feature_version == FEATURE_VERSION
        assert isinstance(value.quality, FeatureQuality)
        if value.value is None:
            assert value.quality is FeatureQuality.INVALID
            assert value.null_reason is not None
        else:
            assert value.quality is not FeatureQuality.INVALID
            assert value.null_reason is None
        if name.startswith("swing_") and value.value is not None:
            assert value.confirmed_ts_ms is not None
            assert value.confirmed_ts_ms <= snapshot.ts_close_ms

    payload = snapshot.to_dict()
    assert payload["graph_version"] == snapshot.graph_version
    assert len(payload["features"]) == 36
    assert len(json.loads(json.dumps(payload))["features"]) == 36


def test_warmup_nulls_are_explicit_not_sentinels():
    state = FeatureState(SYMBOL)
    snapshot = state.update(make_bar(0, 100.0, 100.5, 99.5, 100.0))

    # Always computable from a single bar.
    for name in ("real_body", "body_range_ratio", "upper_shadow", "lower_shadow", "close_position"):
        value = snapshot[name]
        assert value.value is not None
        assert value.quality is FeatureQuality.COMPLETE
    assert snapshot.value("hour_of_day_utc") == 0.0
    assert snapshot.value("impulsive_window") is False

    expected_warmup = {
        "inside_bar",
        "outside_bar",
        "gap_size",
        "gap_dir",
        "atr_14",
        "bb_mid",
        "bb_upper",
        "bb_lower",
        "bb_pct_b",
        "bb_bandwidth",
        "atr_2sigma_active",
        "atr_trend_phase",
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
        "vol_zscore_100",
        "cmf_20",
    }
    for name in expected_warmup:
        value = snapshot[name]
        assert value.value is None, name
        assert value.quality is FeatureQuality.INVALID, name
        assert value.null_reason is NullReason.NOT_YET_AVAILABLE, name
        assert value.is_available is False


# ---------------------------------------------------------------------------
# Candle shape and symmetry
# ---------------------------------------------------------------------------


def test_candle_shape_and_gap_direction():
    state = FeatureState(SYMBOL)
    state.update(make_bar(0, 9.5, 10.0, 9.0, 9.5))
    snapshot = state.update(make_bar(1, 10.2, 11.0, 10.0, 10.5, volume=5.0))

    assert snapshot.value("real_body") == pytest.approx(0.3)
    assert snapshot.value("body_range_ratio") == pytest.approx(0.3)
    assert snapshot.value("upper_shadow") == pytest.approx(0.5)
    assert snapshot.value("lower_shadow") == pytest.approx(0.2)
    assert snapshot.value("close_position") == pytest.approx(0.5)
    assert snapshot.value("inside_bar") is False
    assert snapshot.value("outside_bar") is False
    assert snapshot.value("gap_size") == pytest.approx(0.2)  # open 10.2 > prior high 10.0
    assert snapshot.value("gap_dir") == 1.0

    # A true outside bar that opens inside the prior range: no gap.
    outside = state.update(make_bar(2, 10.5, 11.5, 9.5, 10.0))
    assert outside.value("outside_bar") is True
    assert outside.value("inside_bar") is False
    assert outside.value("gap_size") == 0.0
    assert outside.value("gap_dir") == 0.0

    # Gap down is measured against the prior low and keeps a negative size.
    down = state.update(make_bar(3, 9.0, 9.6, 8.8, 9.2))
    assert down.value("gap_size") == pytest.approx(9.0 - 9.5)
    assert down.value("gap_dir") == -1.0

    inside = state.update(make_bar(4, 9.1, 9.5, 9.0, 9.3))
    assert inside.value("inside_bar") is True
    assert inside.value("outside_bar") is False


def test_candle_long_short_symmetry():
    pivot = 100.0

    def reflect(open_: float, high: float, low: float, close: float) -> tuple[float, float, float, float]:
        # Mirror around the pivot: high and low swap roles.
        return (2 * pivot - open_, 2 * pivot - low, 2 * pivot - high, 2 * pivot - close)

    base = [
        (100.0, 100.5, 99.5, 100.0),
        (100.2, 101.0, 99.8, 100.7),
        (100.7, 101.2, 100.4, 100.5),
    ]
    mirrored = [reflect(*row) for row in base]

    long_state = FeatureState(SYMBOL)
    short_state = FeatureState(SYMBOL)
    long_snap = short_snap = None
    for i, (row, mirror) in enumerate(zip(base, mirrored, strict=True)):
        long_snap = long_state.update(make_bar(i, *row))
        short_snap = short_state.update(make_bar(i, *mirror))

    assert long_snap is not None and short_snap is not None
    assert long_snap.value("real_body") == pytest.approx(short_snap.value("real_body"))
    assert long_snap.value("body_range_ratio") == pytest.approx(short_snap.value("body_range_ratio"))
    assert long_snap.value("upper_shadow") == pytest.approx(short_snap.value("lower_shadow"))
    assert long_snap.value("lower_shadow") == pytest.approx(short_snap.value("upper_shadow"))
    assert long_snap.value("close_position") == pytest.approx(1.0 - short_snap.value("close_position"))
    assert long_snap.value("gap_size") == pytest.approx(-short_snap.value("gap_size"))
    assert long_snap.value("gap_dir") == pytest.approx(-short_snap.value("gap_dir"))


def test_zero_range_and_zero_price_do_not_divide():
    state = FeatureState(SYMBOL)
    snapshot = state.update(make_bar(0, 100.0, 100.0, 100.0, 100.0, volume=0.0))
    assert snapshot.value("body_range_ratio") == 0.0
    assert snapshot.value("close_position") == 0.5
    assert snapshot["body_range_ratio"].quality is FeatureQuality.DEGRADED
    assert snapshot["close_position"].quality is FeatureQuality.DEGRADED
    assert snapshot.value("upper_shadow") == 0.0
    assert snapshot.value("lower_shadow") == 0.0

    zero = [make_bar(i, 0.0, 0.0, 0.0, 0.0, volume=0.0) for i in range(100)]
    zero_snap = compute_snapshot(SYMBOL, zero)
    assert zero_snap is not None
    assert zero_snap.value("atr_14") == 0.0
    assert zero_snap.value("bb_mid") == 0.0
    assert zero_snap.value("bb_upper") == 0.0
    assert zero_snap.value("bb_lower") == 0.0
    assert zero_snap.value("bb_pct_b") == 0.5
    assert zero_snap.value("bb_bandwidth") == 0.0
    assert zero_snap["bb_pct_b"].quality is FeatureQuality.DEGRADED
    assert zero_snap["bb_bandwidth"].quality is FeatureQuality.DEGRADED
    assert zero_snap.value("vol_zscore_100") == 0.0
    assert zero_snap.value("cmf_20") == 0.0
    assert zero_snap["vol_zscore_100"].quality is FeatureQuality.DEGRADED
    assert zero_snap["cmf_20"].quality is FeatureQuality.DEGRADED
    assert zero_snap.value("atr_trend_phase") == 0.0


# ---------------------------------------------------------------------------
# Volatility expansion
# ---------------------------------------------------------------------------


def test_atr_warmup_and_wilder_value():
    state = FeatureState(SYMBOL)
    for i, bar in enumerate(flat_series(40)):
        snapshot = state.update(bar)
        if i < 13:
            assert snapshot.value("atr_14") is None
            assert snapshot["atr_14"].null_reason is NullReason.NOT_YET_AVAILABLE
        else:
            assert snapshot.value("atr_14") == pytest.approx(1.0)

    # 20 ATR observations are needed for the 2-sigma band; the 20th arrives at
    # bar index 32 because ATR itself warms up on index 13.
    state = FeatureState(SYMBOL)
    for i, bar in enumerate(flat_series(40)):
        snapshot = state.update(bar)
        if i <= 31:
            assert snapshot.value("atr_2sigma_active") is None, i
        else:
            assert snapshot.value("atr_2sigma_active") is False, i
        if i <= 22:
            assert snapshot.value("atr_trend_phase") is None, i
        else:
            assert snapshot.value("atr_trend_phase") == 0.0, i


def test_atr_expansion_flags_activation_and_up_phase():
    bars = flat_series(33)
    bars.append(make_bar(33, 100.0, 150.0, 50.0, 100.0))
    snapshot = compute_snapshot(SYMBOL, bars)
    assert snapshot is not None
    assert snapshot.value("atr_2sigma_active") is True
    assert snapshot.value("atr_trend_phase") == 1.0

    manual = manual_atr_series(bars)
    assert snapshot.value("atr_14") == pytest.approx(manual[-1])
    window = [v for v in manual[-20:] if v is not None]
    assert len(window) == 20
    mean, std = mean_std(window)
    assert snapshot.value("atr_2sigma_active") is (manual[-1] > mean + 2.0 * std)


def test_bollinger_matches_independent_batch_formula():
    bars = synthetic_bars(60)
    snapshot = compute_snapshot(SYMBOL, bars)
    assert snapshot is not None

    closes = [b.close for b in bars[-20:]]
    mid, std = mean_std(closes)
    upper = mid + 2.0 * std
    lower = mid - 2.0 * std
    assert snapshot.value("bb_mid") == pytest.approx(mid)
    assert snapshot.value("bb_upper") == pytest.approx(upper)
    assert snapshot.value("bb_lower") == pytest.approx(lower)
    assert snapshot.value("bb_pct_b") == pytest.approx((bars[-1].close - lower) / (upper - lower))
    assert snapshot.value("bb_bandwidth") == pytest.approx((upper - lower) / mid)
    assert snapshot["bb_mid"].quality is FeatureQuality.COMPLETE


# ---------------------------------------------------------------------------
# Location: prefix, windows and confirmed swings
# ---------------------------------------------------------------------------


def test_prefix_and_window_extremes_exclude_the_current_bar():
    bars = flat_series(11)
    bars.append(make_bar(11, 100.0, 200.0, 99.0, 150.0))
    state = FeatureState(SYMBOL)
    for bar in bars:
        snapshot = state.update(bar)

    # The current spike is excluded from both the prior prefix and the window.
    assert snapshot.value("prior_high") == pytest.approx(100.5)
    assert snapshot.value("prior_low") == pytest.approx(99.5)
    assert snapshot.value("window_high_10") == pytest.approx(100.5)
    assert snapshot.value("window_low_10") == pytest.approx(99.5)
    assert snapshot.value("range_height_10") == pytest.approx(1.0)
    assert snapshot.value("window_high_20") is None
    assert snapshot["window_high_20"].null_reason is NullReason.NOT_YET_AVAILABLE

    # Once the spike is a prior bar it enters the window and the prefix.
    later = state.update(make_bar(12, 100.0, 100.5, 99.5, 100.0))
    assert later.value("prior_high") == pytest.approx(200.0)
    assert later.value("window_high_10") == pytest.approx(200.0)
    assert later.value("window_low_10") == pytest.approx(99.0)
    assert later.value("range_height_10") == pytest.approx(101.0)

    manual = [b.high for b in bars[-11:-1]]
    assert max(manual) == pytest.approx(100.5)


def test_window_warmup_boundaries():
    bars = flat_series(60)
    snapshots = compute_snapshots(SYMBOL, bars)
    # window_n uses the n bars before the current bar, so n+1 bars are needed.
    assert snapshots[9].value("window_high_10") is None
    assert snapshots[10].value("window_high_10") == pytest.approx(100.5)
    assert snapshots[19].value("window_high_20") is None
    assert snapshots[20].value("window_high_20") == pytest.approx(100.5)
    assert snapshots[49].value("window_high_50") is None
    assert snapshots[50].value("window_high_50") == pytest.approx(100.5)


def test_swing_pivot_is_emitted_only_after_the_right_window_closes():
    bars = flat_series(41)
    bars[10] = make_bar(10, 100.0, 110.0, 90.0, 100.0)
    snapshots = compute_snapshots(SYMBOL, bars)

    # 10 bars on each side: confirmation happens exactly at index 20.
    assert snapshots[19].value("swing_high_10") is None
    assert snapshots[19].value("swing_low_10") is None
    assert snapshots[20].value("swing_high_10") == pytest.approx(110.0)
    assert snapshots[20].value("swing_low_10") == pytest.approx(90.0)
    assert snapshots[20]["swing_high_10"].confirmed_ts_ms == bars[20].ts_close_ms
    assert snapshots[20]["swing_low_10"].confirmed_ts_ms == bars[20].ts_close_ms

    # The emitted level is frozen: a later bar cannot revise it or move its
    # confirmation timestamp.
    assert snapshots[-1].value("swing_high_10") == pytest.approx(110.0)
    assert snapshots[-1]["swing_high_10"].confirmed_ts_ms == bars[20].ts_close_ms

    # swing_20 cannot exist yet: a 20-left-bar pivot needs index >= 20 and
    # confirmation at index >= 40; flat bar 20 is not a pivot.
    assert snapshots[-1].value("swing_high_20") is None
    assert snapshots[-1]["swing_high_20"].null_reason is NullReason.NO_SIGNIFICANT_PIVOT


def test_swing_20_confirms_after_twenty_right_bars():
    bars = flat_series(41)
    bars[20] = make_bar(20, 100.0, 110.0, 90.0, 100.0)
    snapshots = compute_snapshots(SYMBOL, bars)

    assert snapshots[39].value("swing_high_20") is None
    assert snapshots[40].value("swing_high_20") == pytest.approx(110.0)
    assert snapshots[40].value("swing_low_20") == pytest.approx(90.0)
    assert snapshots[40]["swing_high_20"].confirmed_ts_ms == bars[40].ts_close_ms
    # The 10-window saw the same pivot earlier, at index 30.
    assert snapshots[29].value("swing_high_10") is None
    assert snapshots[30].value("swing_high_10") == pytest.approx(110.0)


def test_swing_significance_requires_one_atr():
    # A tiny pivot (range 0.05) inside a large-ATR tape is not emitted.
    bars = flat_series(41, spread=10.0)
    bars[10] = make_bar(10, 106.0, 106.0, 105.95, 105.97, volume=1.0)
    snapshot = compute_snapshot(SYMBOL, bars)
    assert snapshot is not None
    assert snapshot.value("atr_14") is not None and snapshot.value("atr_14") > 1.0
    assert snapshot.value("swing_high_10") is None
    assert snapshot["swing_high_10"].null_reason is NullReason.NO_SIGNIFICANT_PIVOT

    # A wide pivot in a quiet tape is emitted.
    quiet = flat_series(41)
    quiet[10] = make_bar(10, 100.0, 110.0, 90.0, 100.0)
    wide = compute_snapshot(SYMBOL, quiet)
    assert wide is not None
    assert wide.value("swing_high_10") == pytest.approx(110.0)


def test_prefix_extremes_survive_buffer_rollover():
    bars = flat_series(150)
    bars[0] = make_bar(0, 100.0, 1000.0, 50.0, 100.0)
    config = FeatureConfig(history=120)
    snapshot = compute_snapshot(SYMBOL, bars, config)
    assert snapshot is not None
    assert snapshot.value("prior_high") == pytest.approx(1000.0)
    assert snapshot.value("prior_low") == pytest.approx(50.0)
    # The rolling 50-bar window no longer sees the first bar.
    assert snapshot.value("window_high_50") == pytest.approx(100.5)


# ---------------------------------------------------------------------------
# Participation and session
# ---------------------------------------------------------------------------


def test_vol_zscore_matches_independent_formula():
    bars = flat_series(100, volume=1.0)
    bars[-1] = make_bar(99, 100.0, 100.5, 99.5, 100.0, volume=3.0)
    snapshot = compute_snapshot(SYMBOL, bars)
    assert snapshot is not None
    mean, std = mean_std([b.volume for b in bars])
    assert snapshot.value("vol_zscore_100") == pytest.approx((3.0 - mean) / std)
    assert snapshot["vol_zscore_100"].quality is FeatureQuality.COMPLETE

    early = compute_snapshots(SYMBOL, flat_series(99))
    assert early[-1].value("vol_zscore_100") is None
    assert early[-1]["vol_zscore_100"].null_reason is NullReason.NOT_YET_AVAILABLE


def test_cmf_20_matches_sign_and_magnitude():
    buying = [make_bar(i, 100.0, 101.0, 99.0, 101.0, volume=2.0) for i in range(20)]
    snapshot = compute_snapshot(SYMBOL, buying)
    assert snapshot is not None
    assert snapshot.value("cmf_20") == pytest.approx(1.0)

    selling = [make_bar(i, 100.0, 101.0, 99.0, 99.0, volume=2.0) for i in range(20)]
    snapshot = compute_snapshot(SYMBOL, selling)
    assert snapshot is not None
    assert snapshot.value("cmf_20") == pytest.approx(-1.0)

    early = compute_snapshots(SYMBOL, buying[:19])
    assert early[-1].value("cmf_20") is None
    assert early[-1]["cmf_20"].null_reason is NullReason.NOT_YET_AVAILABLE


def test_session_features_use_utc_close_hour():
    state = FeatureState(SYMBOL)
    bars = [
        make_bar(479, 100.0, 100.5, 99.5, 100.0),  # closes 08:00 UTC
        make_bar(1079, 100.0, 100.5, 99.5, 100.0),  # closes 18:00 UTC
        make_bar(1019, 100.0, 100.5, 99.5, 100.0),  # closes 17:00 UTC
    ]
    morning = state.update(bars[0])
    assert morning.value("hour_of_day_utc") == 8.0
    assert morning.value("impulsive_window") is True

    evening = state.update(bars[1])
    assert evening.value("hour_of_day_utc") == 18.0
    assert evening.value("impulsive_window") is False

    # Out-of-order timestamps are rejected, so build a fresh state for 17:00.
    late = FeatureState(SYMBOL).update(bars[2])
    assert late.value("hour_of_day_utc") == 17.0
    assert late.value("impulsive_window") is True


# ---------------------------------------------------------------------------
# Point-in-time and prefix/full equality
# ---------------------------------------------------------------------------


def test_future_bar_cannot_change_an_earlier_snapshot():
    bars = synthetic_bars(150)
    state = FeatureState(SYMBOL)
    for bar in bars[:120]:
        state.update(bar)
    pinned = state.last_snapshot
    assert pinned is not None
    pinned_payload = pinned.to_dict()

    fresh = compute_snapshot(SYMBOL, bars[:120])
    assert fresh is not None
    assert fresh.to_dict() == pinned_payload

    for bar in bars[120:]:
        state.update(bar)
    assert pinned.to_dict() == pinned_payload
    assert pinned.available_time_ms == bars[119].ts_close_ms
    for value in pinned.values.values():
        assert value.available_time_ms == bars[119].ts_close_ms
        assert value.input_ts_ms == bars[119].ts_open_ms

    assert state.last_snapshot is not None
    assert state.last_snapshot.ts_close_ms == bars[-1].ts_close_ms
    assert state.last_snapshot.to_dict() != pinned_payload


def test_incremental_snapshot_equals_independent_batch_recomputation():
    bars = synthetic_bars(150, seed=11)
    snapshot = compute_snapshot(SYMBOL, bars)
    assert snapshot is not None

    last = bars[-1]
    bar_range = last.high - last.low
    assert snapshot.value("real_body") == pytest.approx(abs(last.close - last.open))
    assert snapshot.value("body_range_ratio") == pytest.approx(abs(last.close - last.open) / bar_range)
    assert snapshot.value("upper_shadow") == pytest.approx(last.high - max(last.open, last.close))
    assert snapshot.value("lower_shadow") == pytest.approx(min(last.open, last.close) - last.low)
    assert snapshot.value("close_position") == pytest.approx((last.close - last.low) / bar_range)

    previous = bars[-2]
    assert snapshot.value("inside_bar") == (last.high <= previous.high and last.low >= previous.low)
    assert snapshot.value("outside_bar") == (last.high >= previous.high and last.low <= previous.low)
    if last.open > previous.high:
        assert snapshot.value("gap_size") == pytest.approx(last.open - previous.high)
        assert snapshot.value("gap_dir") == 1.0
    elif last.open < previous.low:
        assert snapshot.value("gap_size") == pytest.approx(last.open - previous.low)
        assert snapshot.value("gap_dir") == -1.0
    else:
        assert snapshot.value("gap_size") == 0.0
        assert snapshot.value("gap_dir") == 0.0

    assert snapshot.value("prior_high") == pytest.approx(max(b.high for b in bars[:-1]))
    assert snapshot.value("prior_low") == pytest.approx(min(b.low for b in bars[:-1]))
    for n in (10, 20, 50):
        window = bars[-(n + 1) : -1]
        high = max(b.high for b in window)
        low = min(b.low for b in window)
        assert snapshot.value(f"window_high_{n}") == pytest.approx(high)
        assert snapshot.value(f"window_low_{n}") == pytest.approx(low)
        assert snapshot.value(f"range_height_{n}") == pytest.approx(high - low)

    closes = [b.close for b in bars[-20:]]
    mid, std = mean_std(closes)
    assert snapshot.value("bb_mid") == pytest.approx(mid)
    assert snapshot.value("bb_upper") == pytest.approx(mid + 2.0 * std)
    assert snapshot.value("bb_lower") == pytest.approx(mid - 2.0 * std)

    manual_atr = manual_atr_series(bars)
    assert snapshot.value("atr_14") == pytest.approx(manual_atr[-1])
    atr_series = [v for v in manual_atr if v is not None]
    assert snapshot.value("atr_trend_phase") == pytest.approx(
        1.0 if atr_series[-1] > atr_series[-11] else -1.0 if atr_series[-1] < atr_series[-11] else 0.0
    )
    atr_window = atr_series[-20:]
    atr_mean, atr_std = mean_std(atr_window)
    assert snapshot.value("atr_2sigma_active") is (atr_series[-1] > atr_mean + 2.0 * atr_std)

    volumes = [b.volume for b in bars[-100:]]
    volume_mean, volume_std = mean_std(volumes)
    assert snapshot.value("vol_zscore_100") == pytest.approx((last.volume - volume_mean) / volume_std)

    money_flow = 0.0
    volume_sum = 0.0
    for bar in bars[-20:]:
        rng = bar.high - bar.low
        money_flow += ((bar.close - bar.low) - (bar.high - bar.close)) / rng * bar.volume
        volume_sum += bar.volume
    assert snapshot.value("cmf_20") == pytest.approx(money_flow / volume_sum)


# ---------------------------------------------------------------------------
# Degradation, feed discipline, reset
# ---------------------------------------------------------------------------


def test_gap_marks_only_the_windows_that_contain_it():
    bars = flat_series(30)
    gapped = bars[:21] + [make_bar(i, 100.0, 100.5, 99.5, 100.0) for i in range(26, 41)]
    state = FeatureState(SYMBOL)
    for bar in gapped:
        state.update(bar)

    snapshots = compute_snapshots(SYMBOL, gapped)
    after_hole = snapshots[21]
    assert after_hole.ts_open_ms == T0 + 26 * MINUTE_MS
    assert after_hole["prior_high"].quality is FeatureQuality.DEGRADED
    assert after_hole["atr_14"].quality is FeatureQuality.DEGRADED
    assert after_hole["bb_mid"].quality is FeatureQuality.DEGRADED
    # The 10-bar window excludes the current bar, so the hole is only inside
    # it one bar later.
    assert after_hole["window_high_10"].quality is FeatureQuality.COMPLETE
    assert snapshots[22]["window_high_10"].quality is FeatureQuality.DEGRADED

    # Later, the 10-bar window no longer contains the hole, but ATR and the
    # unbounded prefix remain honestly degraded.
    late = state.last_snapshot
    assert late is not None
    assert late["window_high_10"].quality is FeatureQuality.COMPLETE
    assert late["prior_high"].quality is FeatureQuality.DEGRADED
    assert late["atr_14"].quality is FeatureQuality.DEGRADED


def test_feed_discipline_and_reset():
    state = FeatureState(SYMBOL)
    bar = make_bar(0, 100.0, 100.5, 99.5, 100.0)
    state.update(bar)

    with pytest.raises(ValueError):
        state.update(bar)
    with pytest.raises(ValueError):
        state.update(make_bar(-1, 100.0, 100.5, 99.5, 100.0))
    with pytest.raises(ValueError):
        state.update(make_bar(1, 100.0, 100.5, 99.5, 100.0, symbol="OTHERUSDT"))

    state.reset()
    assert state.bar_count == 0
    assert state.last_snapshot is None
    bars = synthetic_bars(30)
    for bar in bars:
        state.update(bar)
    assert state.last_snapshot is not None
    fresh = compute_snapshot(SYMBOL, bars)
    assert fresh is not None
    assert state.last_snapshot.to_dict() == fresh.to_dict()


def test_config_rejects_windows_that_exceed_history():
    with pytest.raises(ValueError):
        FeatureConfig(history=40, swing_windows=(20,))
    with pytest.raises(ValueError):
        FeatureConfig(window_sizes=(10, 5))
    with pytest.raises(ValueError):
        FeatureConfig(impulsive_start_hour=18, impulsive_end_hour=8)


def test_feature_graph_name_is_stable():
    # The hash must not silently drift when someone edits the module: this
    # pins the human-readable graph name and version string.
    assert FEATURE_GRAPH_NAME == "tradebot4.market_state.features.v1"
    assert FEATURE_VERSION == "features_v1"
