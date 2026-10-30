"""Load canonical parquet bars into core / Nautilus objects."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import polars as pl

from bot.data.storage import Paths

BAR_COLUMNS = [
    "ts_open_ms",
    "ts_close_ms",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "quote_volume",
]


def month_partitions(bars_dir: Path, symbol: str, start_ms: int, end_ms: int) -> list[Path]:
    out: list[Path] = []
    start = datetime.fromtimestamp(start_ms / 1000, tz=UTC)
    end = datetime.fromtimestamp(end_ms / 1000, tz=UTC)
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month):
        p = bars_dir / f"symbol={symbol}" / f"year={y:04d}" / f"month={m:02d}" / "data.parquet"
        if p.exists():
            out.append(p)
        m += 1
        if m == 13:
            y, m = y + 1, 1
    return out


def load_bars_frame(paths: Paths, symbol: str, start_ms: int, end_ms: int) -> pl.DataFrame:
    files = month_partitions(paths.bars_1m, symbol, start_ms, end_ms)
    if not files:
        return pl.DataFrame()
    df = pl.read_parquet(files, columns=["symbol", *BAR_COLUMNS])
    df = df.filter((pl.col("ts_close_ms") > start_ms) & (pl.col("ts_close_ms") <= end_ms))
    return df.sort("ts_open_ms")


def load_bars_core(paths: Paths, symbol: str, start_ms: int, end_ms: int) -> list[dict]:
    return load_bars_frame(paths, symbol, start_ms, end_ms).to_dicts()


def load_1d_frame(paths: Paths, symbol: str, start_ms: int, end_ms: int) -> pl.DataFrame:
    files = month_partitions(paths.bars_1d, symbol, start_ms, end_ms)
    if not files:
        return pl.DataFrame()
    df = pl.read_parquet(files)
    return df.filter((pl.col("ts_close_ms") > start_ms) & (pl.col("ts_close_ms") <= end_ms)).sort("ts_open_ms")


def calendar_chunks(start_ms: int, end_ms: int, chunk_days: int) -> list[tuple[int, int]]:
    step = chunk_days * 86_400_000
    out = []
    cur = start_ms
    while cur < end_ms:
        nxt = min(cur + step, end_ms)
        out.append((cur, nxt))
        cur = nxt
    return out
