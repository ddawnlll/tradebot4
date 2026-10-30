"""Signals and other datasets are partitioned by symbol and month."""

from __future__ import annotations

import polars as pl

from bot.backtest.export import write_dataset

T0 = 1_735_689_600_000


def test_partitioned_dataset_roundtrip(tmp_path) -> None:
    df = pl.DataFrame(
        {
            "symbol": ["A", "A", "B"],
            "ts_ms": [T0, T0 + 40 * 86_400_000, T0],
            "value": [1.0, 2.0, 3.0],
        }
    )
    written = write_dataset(df, tmp_path, "signals", "ts_ms", ("symbol", "year", "month"))
    assert written == 3
    assert (tmp_path / "signals" / "symbol=A" / "year=2025" / "month=01").is_dir()
    assert (tmp_path / "signals" / "symbol=A" / "year=2025" / "month=02").is_dir()
    files = sorted((tmp_path / "signals").rglob("*.parquet"))
    assert len(files) == 3
    loaded = pl.read_parquet(files)
    assert loaded.height == 3
    assert sorted(loaded["value"].to_list()) == [1.0, 2.0, 3.0]
