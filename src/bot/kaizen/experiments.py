"""Append-only experiment ledger.

Every backtest configuration tried in a research round is recorded, including
failures and parameter-grid neighbours. The ledger is a parquet file so it can
be queried with DuckDB/Polars without a database service.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import polars as pl

COLUMNS: dict[str, pl.DataType] = {
    "ts_utc": pl.Utf8,
    "round": pl.Utf8,
    "phase": pl.Utf8,
    "hypothesis": pl.Utf8,
    "branch": pl.Utf8,
    "data_slice": pl.Utf8,
    "start_ms": pl.Int64,
    "end_ms": pl.Int64,
    "symbols": pl.Int64,
    "trial_number": pl.Int64,
    "config_hash": pl.Utf8,
    "run_id": pl.Utf8,
    "params_json": pl.Utf8,
    "metrics_json": pl.Utf8,
    "notes": pl.Utf8,
}


def ledger_path() -> Path:
    return Path(__file__).resolve().parents[3] / "kaizen" / "experiments.parquet"


def next_trial_number(path: Path | None = None) -> int:
    path = path or ledger_path()
    if not path.exists():
        return 1
    try:
        df = pl.read_parquet(path, columns=["trial_number"])
        return int(df["trial_number"].max() or 0) + 1
    except Exception:
        return 1


def record_experiment(record: dict[str, Any], path: Path | None = None) -> dict[str, Any]:
    path = path or ledger_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    row = {key: record.get(key) for key in COLUMNS}
    row["ts_utc"] = row.get("ts_utc") or datetime.now(UTC).isoformat()
    row["trial_number"] = int(row.get("trial_number") or next_trial_number(path))
    for key in ("params_json", "metrics_json"):
        value = row.get(key)
        if isinstance(value, (dict, list)):
            row[key] = json.dumps(value, sort_keys=True, default=str)
    if path.exists():
        existing = pl.read_parquet(path)
        if existing.height:
            df = pl.concat([existing, pl.DataFrame([row], schema=COLUMNS)], how="vertical_relaxed")
        else:
            df = pl.DataFrame([row], schema=COLUMNS)
    else:
        df = pl.DataFrame([row], schema=COLUMNS)
    df.write_parquet(path, compression="zstd", statistics=True)
    return row
