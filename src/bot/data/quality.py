"""Data quality checks and report."""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import polars as pl

from bot.data.loader import load_bars_frame
from bot.data.normalize import parquet_files
from bot.data.storage import Paths, dataset_hash, file_sha256

log = logging.getLogger(__name__)


@dataclass(slots=True)
class SymbolQuality:
    symbol: str
    bars: int = 0
    expected_bars: int = 0
    missing_bars: int = 0
    missing_pct: float = 0.0
    duplicates: int = 0
    ohlc_violations: int = 0
    nonpositive_prices: int = 0
    zero_volume_bars: int = 0
    timestamp_misalignment: int = 0
    extreme_1m_returns: int = 0
    first_ts_ms: int | None = None
    last_ts_ms: int | None = None
    notes: list[str] = field(default_factory=list)


def check_symbol(paths: Paths, symbol: str, start_ms: int, end_ms: int) -> SymbolQuality:
    df = load_bars_frame(paths, symbol, start_ms, end_ms)
    q = SymbolQuality(symbol=symbol)
    if df.height == 0:
        q.notes.append("no data in range")
        return q
    q.bars = df.height
    q.expected_bars = max(0, (end_ms - start_ms) // 60_000)
    q.missing_bars = max(0, q.expected_bars - q.bars)
    q.missing_pct = 100.0 * q.missing_bars / q.expected_bars if q.expected_bars else 0.0
    q.duplicates = df.height - df.unique(subset=["ts_open_ms"]).height
    q.ohlc_violations = df.filter(
        (pl.col("high") < pl.col("low"))
        | (pl.col("high") < pl.col("open"))
        | (pl.col("high") < pl.col("close"))
        | (pl.col("low") > pl.col("open"))
        | (pl.col("low") > pl.col("close"))
    ).height
    q.nonpositive_prices = df.filter((pl.col("close") <= 0) | (pl.col("open") <= 0)).height
    q.zero_volume_bars = df.filter(pl.col("volume") <= 0).height
    q.timestamp_misalignment = df.filter((pl.col("ts_close_ms") - pl.col("ts_open_ms")) != 60_000).height
    rets = (df["close"] / df["close"].shift(1) - 1).abs()
    q.extreme_1m_returns = int((rets > 0.20).sum())
    q.first_ts_ms = int(df["ts_open_ms"][0])
    q.last_ts_ms = int(df["ts_open_ms"][-1])
    return q


def check_funding(paths: Paths, symbols: list[str], start_ms: int, end_ms: int) -> dict:
    p = paths.funding / "funding.parquet"
    if not p.exists():
        return {"error": "missing funding.parquet"}
    df = pl.read_parquet(p).filter((pl.col("ts_ms") >= start_ms) & (pl.col("ts_ms") <= end_ms))
    if df.height == 0:
        return {"rows": 0}
    out = {
        "rows": df.height,
        "symbols": df["symbol"].n_unique(),
        "interval_hours": df.group_by("interval_hours").agg(pl.len()).sort("interval_hours").to_dicts(),
        "rate_min": float(df["rate"].min()),
        "rate_max": float(df["rate"].max()),
        "rate_abs_mean": float(df["rate"].abs().mean()),
        "extreme_rates": int((df["rate"].abs() > 0.01).sum()),
        "per_symbol": {},
    }
    for sym, sub in df.group_by("symbol"):
        s = sym[0] if isinstance(sym, tuple) else sym
        out["per_symbol"][s] = {"rows": sub.height, "hours": int(sub["interval_hours"].mode()[0]) if sub.height else None}
    return out


def run_quality(paths: Paths | None = None, symbols: list[str] | None = None, start_ms: int = 0, end_ms: int = 0, out_dir: Path | None = None) -> dict:
    paths = paths or Paths()
    out_dir = out_dir or paths.reports
    out_dir.mkdir(parents=True, exist_ok=True)
    if not symbols:
        symbols = sorted({p.parents[2].name.split("=")[1] for p in parquet_files(paths)})
    results = [check_symbol(paths, s, start_ms, end_ms) for s in symbols]
    funding = check_funding(paths, symbols, start_ms, end_ms)
    payload = {
        "generated": datetime.now(UTC).isoformat(),
        "range": {"start_ms": start_ms, "end_ms": end_ms},
        "symbols": [asdict(r) for r in results],
        "funding": funding,
        "dataset_hash": dataset_hash(parquet_files(paths)),
    }
    (out_dir / "data_quality.json").write_text(json.dumps(payload, indent=2))
    (out_dir / "data_quality.md").write_text(render_markdown(payload))
    return payload


def render_markdown(payload: dict) -> str:
    rows = payload["symbols"]
    lines = [
        "# Data quality report",
        "",
        f"Generated: {payload['generated']}",
        f"Dataset hash: `{payload['dataset_hash']}`",
        "",
        "| symbol | bars | missing | missing % | ohlc bad | nonpos | zero vol | misaligned | extreme 1m |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for r in sorted(rows, key=lambda x: x["symbol"]):
        lines.append(
            f"| {r['symbol']} | {r['bars']} | {r['missing_bars']} | {r['missing_pct']:.2f} | {r['ohlc_violations']} | "
            f"{r['nonpositive_prices']} | {r['zero_volume_bars']} | {r['timestamp_misalignment']} | {r['extreme_1m_returns']} |"
        )
    lines += ["", "## Funding", "", "```json", json.dumps(payload["funding"], indent=2)[:4000], "```", ""]
    return "\n".join(lines)


def file_hashes(paths: Paths) -> dict[str, str]:
    return {str(p.relative_to(paths.root)): file_sha256(p) for p in parquet_files(paths)}
