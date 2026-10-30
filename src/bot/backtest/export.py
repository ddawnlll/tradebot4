"""Partitioned parquet export for large run datasets.

Signals can reach hundreds of thousands of rows per run. They are written as
``signals/symbol=.../year=.../month=.../part.parquet`` so no single file grows
without bound. Trades, fills and orders are small and are also written as a
single consolidated file for compatibility, plus a partitioned copy.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import polars as pl


def write_dataset(
    df: pl.DataFrame,
    run_dir: Path,
    name: str,
    ts_col: str,
    partition_by: tuple[str, ...],
) -> int:
    if df.height == 0:
        return 0
    out = Path(run_dir) / name
    if out.exists():
        shutil.rmtree(out)
    work = df.with_columns(
        pl.from_epoch(pl.col(ts_col), time_unit="ms").dt.year().alias("year"),
        pl.from_epoch(pl.col(ts_col), time_unit="ms").dt.month().alias("month"),
    )
    columns = list(partition_by)
    written = 0
    for key, part in work.group_by(columns, maintain_order=True):
        key_tuple = key if isinstance(key, tuple) else (key,)
        segments = []
        for col, value in zip(columns, key_tuple, strict=True):
            if col in ("year", "month"):
                segments.append(f"{col}={int(value):04d}" if col == "year" else f"{col}={int(value):02d}")
            else:
                segments.append(f"{col}={value}")
        target = out.joinpath(*segments)
        target.mkdir(parents=True, exist_ok=True)
        part.drop([c for c in ("year", "month") if c in part.columns]).write_parquet(
            target / "part.parquet", compression="zstd", statistics=True
        )
        written += part.height
    return written
