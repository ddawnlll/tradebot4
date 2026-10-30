"""Missing funding data must abort the run, not silently become zero."""

from __future__ import annotations

import polars as pl
import pytest

from bot.backtest.funding import validate_funding_coverage
from bot.data.storage import Paths

T0 = 1_735_689_600_000  # 2025-01-01Z


def _paths(tmp_path) -> Paths:
    paths = Paths(root=tmp_path)
    paths.ensure()
    return paths


def _write_bars(paths: Paths) -> None:
    rows = []
    for i in range(120):
        ts = T0 + i * 60_000
        rows.append(
            {
                "symbol": "TESTUSDT",
                "ts_open_ms": ts,
                "ts_close_ms": ts + 60_000,
                "open": 100.0,
                "high": 101.0,
                "low": 99.0,
                "close": 100.5,
                "volume": 10.0,
                "quote_volume": 1000.0,
            }
        )
    path = paths.bars_1m / "symbol=TESTUSDT" / "year=2025" / "month=01" / "data.parquet"
    path.parent.mkdir(parents=True)
    pl.DataFrame(rows).write_parquet(path)


def _write_funding(paths: Paths, *, drop: bool = False) -> None:
    rows = [
        {"symbol": "TESTUSDT", "ts_ms": T0 + h * 3_600_000, "interval_hours": 1, "rate": 0.0001}
        for h in range(0, 3)
    ]
    if drop:
        rows = []
    path = paths.funding / "funding.parquet"
    pl.DataFrame(rows, schema={"symbol": pl.Utf8, "ts_ms": pl.Int64, "interval_hours": pl.Int64, "rate": pl.Float64}).write_parquet(path)


def test_complete_funding_passes(tmp_path) -> None:
    paths = _paths(tmp_path)
    _write_bars(paths)
    _write_funding(paths)
    validate_funding_coverage(paths, ["TESTUSDT"], T0, T0 + 120 * 60_000)


def test_missing_funding_raises(tmp_path) -> None:
    paths = _paths(tmp_path)
    _write_bars(paths)
    _write_funding(paths, drop=True)
    with pytest.raises(RuntimeError, match="funding coverage incomplete"):
        validate_funding_coverage(paths, ["TESTUSDT"], T0, T0 + 120 * 60_000)


def test_funding_gap_raises(tmp_path) -> None:
    paths = _paths(tmp_path)
    _write_bars(paths)
    rows = [
        {"symbol": "TESTUSDT", "ts_ms": T0, "interval_hours": 1, "rate": 0.0001},
        {"symbol": "TESTUSDT", "ts_ms": T0 + 100 * 60_000, "interval_hours": 1, "rate": 0.0001},
    ]
    pl.DataFrame(rows, schema={"symbol": pl.Utf8, "ts_ms": pl.Int64, "interval_hours": pl.Int64, "rate": pl.Float64}).write_parquet(
        paths.funding / "funding.parquet"
    )
    with pytest.raises(RuntimeError, match="funding gap"):
        validate_funding_coverage(paths, ["TESTUSDT"], T0, T0 + 120 * 60_000, max_gap_ms=50 * 60_000)
