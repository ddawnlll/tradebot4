"""Normalize raw Binance zips into canonical parquet.

Canonical bar schema:
  symbol, ts_open_ms, ts_close_ms, open, high, low, close,
  volume, quote_volume, trades, taker_buy_volume, taker_buy_quote_volume

``ts_close_ms`` is exclusive (Binance's ``close_time`` + 1).
Partitioning: ``bars_1m/symbol=<SYM>/year=<YYYY>/month=<MM>/data.parquet``.
"""

from __future__ import annotations

import io
import logging
import zipfile
from datetime import UTC, datetime
from pathlib import Path

import polars as pl

from bot.data.storage import Paths

log = logging.getLogger(__name__)

BAR_SCHEMA = {
    "symbol": pl.Utf8,
    "ts_open_ms": pl.Int64,
    "ts_close_ms": pl.Int64,
    "open": pl.Float64,
    "high": pl.Float64,
    "low": pl.Float64,
    "close": pl.Float64,
    "volume": pl.Float64,
    "quote_volume": pl.Float64,
    "trades": pl.Int64,
    "taker_buy_volume": pl.Float64,
    "taker_buy_quote_volume": pl.Float64,
}

KLINES_COLS = [
    "open_time",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "close_time",
    "quote_volume",
    "count",
    "taker_buy_volume",
    "taker_buy_quote_volume",
    "ignore",
]


def _read_zip_csv(path: Path, has_header: bool) -> pl.DataFrame:
    frames: list[pl.DataFrame] = []
    with zipfile.ZipFile(path) as zf:
        for name in zf.namelist():
            if not name.endswith(".csv"):
                continue
            raw = zf.read(name)
            try:
                df = pl.read_csv(
                    io.BytesIO(raw),
                    has_header=has_header,
                    schema_overrides={
                        "open_time": pl.Int64,
                        "close_time": pl.Int64,
                        "count": pl.Int64,
                        "ignore": pl.Int64,
                    },
                    try_parse_dates=False,
                )
            except Exception as exc:  # pragma: no cover - corrupt file guard
                log.warning("cannot parse %s/%s: %s", path, name, exc)
                continue
            frames.append(df)
    if not frames:
        return pl.DataFrame()
    return pl.concat(frames, how="vertical_relaxed")


def normalize_klines_file(path: Path, symbol: str) -> pl.DataFrame:
    df = _read_zip_csv(path, has_header=True)
    if df.height == 0:
        return pl.DataFrame(schema=BAR_SCHEMA)
    if df.columns[: len(KLINES_COLS)] != KLINES_COLS:
        # Older files occasionally ship without a header.
        df = _read_zip_csv(path, has_header=False)
        df.columns = KLINES_COLS[: df.width]
    out = df.select(
        pl.lit(symbol).alias("symbol"),
        pl.col("open_time").cast(pl.Int64).alias("ts_open_ms"),
        (pl.col("close_time").cast(pl.Int64) + 1).alias("ts_close_ms"),
        pl.col("open").cast(pl.Float64),
        pl.col("high").cast(pl.Float64),
        pl.col("low").cast(pl.Float64),
        pl.col("close").cast(pl.Float64),
        pl.col("volume").cast(pl.Float64),
        pl.col("quote_volume").cast(pl.Float64),
        pl.col("count").cast(pl.Int64).alias("trades"),
        pl.col("taker_buy_volume").cast(pl.Float64),
        pl.col("taker_buy_quote_volume").cast(pl.Float64),
    )
    return out.filter(pl.col("ts_open_ms").is_not_null()).unique(subset=["ts_open_ms"], keep="last").sort("ts_open_ms")


def normalize_bars(raw_files: list[Path], bars_dir: Path, *, interval: str = "1m") -> dict:
    """Normalize a set of raw zips into monthly partitions."""
    stats = {"files": 0, "rows": 0, "skipped": 0, "months": 0}
    by_partition: dict[tuple[str, int, int], list[pl.DataFrame]] = {}
    for p in raw_files:
        if p.suffix != ".zip":
            continue
        symbol = p.name.split(f"-{interval}-")[0]
        df = normalize_klines_file(p, symbol)
        if df.height == 0:
            stats["skipped"] += 1
            continue
        stats["files"] += 1
        stats["rows"] += df.height
        df = df.with_columns(
            pl.from_epoch(pl.col("ts_open_ms"), time_unit="ms").dt.year().alias("year"),
            pl.from_epoch(pl.col("ts_open_ms"), time_unit="ms").dt.month().alias("month"),
        )
        for (sym, y, m), part in df.group_by(["symbol", "year", "month"], maintain_order=True):
            by_partition.setdefault((sym, int(y), int(m)), []).append(part.drop(["year", "month"]))

    for (symbol, year, month), parts in sorted(by_partition.items()):
        out = pl.concat(parts, how="vertical_relaxed").unique(subset=["ts_open_ms"], keep="last").sort("ts_open_ms")
        out = out.cast(BAR_SCHEMA)
        target = bars_dir / f"symbol={symbol}" / f"year={year:04d}" / f"month={month:02d}" / "data.parquet"
        target.parent.mkdir(parents=True, exist_ok=True)
        out.write_parquet(target, compression="zstd", statistics=True)
        stats["months"] += 1
    return stats


def normalize_funding_file(path: Path, symbol: str) -> pl.DataFrame:
    df = _read_zip_csv(path, has_header=True)
    if df.height == 0:
        return pl.DataFrame()
    return df.select(
        pl.lit(symbol).alias("symbol"),
        pl.col("calc_time").cast(pl.Int64).alias("ts_ms"),
        pl.col("funding_interval_hours").cast(pl.Int64).alias("interval_hours"),
        pl.col("last_funding_rate").cast(pl.Float64).alias("rate"),
    ).unique(subset=["ts_ms"], keep="last").sort("ts_ms")


def normalize_mark_price_file(path: Path, symbol: str) -> pl.DataFrame:
    df = _read_zip_csv(path, has_header=True)
    if df.height == 0:
        return pl.DataFrame()
    return df.select(
        pl.lit(symbol).alias("symbol"),
        pl.col("open_time").cast(pl.Int64).alias("ts_open_ms"),
        (pl.col("close_time").cast(pl.Int64) + 1).alias("ts_close_ms"),
        pl.col("open").cast(pl.Float64),
        pl.col("high").cast(pl.Float64),
        pl.col("low").cast(pl.Float64),
        pl.col("close").cast(pl.Float64),
    ).unique(subset=["ts_open_ms"], keep="last").sort("ts_open_ms")


def normalize_funding(raw_files: list[Path], out_dir: Path) -> dict:
    parts: list[pl.DataFrame] = []
    for p in raw_files:
        if p.suffix != ".zip":
            continue
        symbol = p.name.split("-fundingRate-")[0]
        df = normalize_funding_file(p, symbol)
        if df.height:
            parts.append(df)
    if not parts:
        return {"rows": 0, "files": 0}
    out = pl.concat(parts, how="vertical_relaxed").unique(subset=["symbol", "ts_ms"], keep="last").sort(["symbol", "ts_ms"])
    target = out_dir / "funding.parquet"
    target.parent.mkdir(parents=True, exist_ok=True)
    out.write_parquet(target, compression="zstd", statistics=True)
    return {"rows": out.height, "files": len(parts)}


def normalize_mark_price(raw_files: list[Path], out_dir: Path) -> dict:
    parts: list[pl.DataFrame] = []
    for p in raw_files:
        if p.suffix != ".zip":
            continue
        symbol = p.name.split("-markPriceKlines-")[0]
        df = normalize_mark_price_file(p, symbol)
        if df.height:
            parts.append(df)
    if not parts:
        return {"rows": 0, "files": 0}
    out = pl.concat(parts, how="vertical_relaxed").unique(subset=["symbol", "ts_open_ms"], keep="last").sort(["symbol", "ts_open_ms"])
    target = out_dir / "mark_price_1m.parquet"
    target.parent.mkdir(parents=True, exist_ok=True)
    out.write_parquet(target, compression="zstd", statistics=True)
    return {"rows": out.height, "files": len(parts)}


def raw_files_for(paths: Paths, kind: str, interval: str = "1m") -> list[Path]:
    if kind == "klines":
        base = paths.raw / "klines" / interval
    elif kind == "markPriceKlines":
        base = paths.raw / "markPriceKlines" / interval
    else:
        base = paths.raw / "fundingRate"
    return sorted(base.rglob("*.zip")) if base.exists() else []


def parquet_files(paths: Paths, interval: str = "1m") -> list[Path]:
    base = paths.bars_1m if interval == "1m" else paths.bars_1d
    return sorted(base.rglob("*.parquet")) if base.exists() else []


def month_partition_path(bars_dir: Path, symbol: str, year: int, month: int) -> Path:
    return bars_dir / f"symbol={symbol}" / f"year={year:04d}" / f"month={month:02d}" / "data.parquet"


def month_key(ts_ms: int) -> tuple[int, int]:
    dt = datetime.fromtimestamp(ts_ms / 1000, tz=UTC)
    return dt.year, dt.month
