"""Journal ETL: SQLite journals -> consolidated parquet + DuckDB views."""

from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path

import duckdb
import polars as pl

from bot.data.storage import Paths

log = logging.getLogger(__name__)


def read_events(db_path: Path) -> pl.DataFrame:
    if not Path(db_path).exists():
        return pl.DataFrame()
    conn = sqlite3.connect(db_path)
    try:
        events = pl.read_database(
            "SELECT event_id, schema_version, source, event_type, ts_event_ms, ts_recorded_ms, symbol, signal_id,"
            " strategy_version, config_hash, run_id, payload FROM events",
            conn,
            infer_schema_length=None,
        )
        tags = pl.read_database("SELECT * FROM tags", conn, infer_schema_length=None)
    finally:
        conn.close()
    if events.height and tags.height:
        events = events.with_columns(pl.lit(json.dumps({"tags": tags.height})).alias("_tags_marker")).drop("_tags_marker")
    return events


def export_all(paths: Paths | None = None) -> dict:
    paths = paths or Paths()
    paths.journal.mkdir(parents=True, exist_ok=True)
    dbs = sorted(set(list((paths.root / "runs").glob("*/journal.sqlite")) + list(paths.journal.glob("*.sqlite"))))
    frames = [read_events(db) for db in dbs]
    frames = [f for f in frames if f.height]
    if not frames:
        return {"dbs": 0, "events": 0}
    events = pl.concat(frames, how="vertical_relaxed").unique(subset=["event_id"], keep="last")
    events = events.with_columns(
        pl.from_epoch(pl.col("ts_event_ms"), time_unit="ms").dt.year().alias("year"),
        pl.from_epoch(pl.col("ts_event_ms"), time_unit="ms").dt.month().alias("month"),
    )
    target = paths.journal / "events.parquet"
    events.write_parquet(target, compression="zstd", statistics=True)
    tags: list[pl.DataFrame] = []
    for db in dbs:
        conn = sqlite3.connect(db)
        try:
            t = pl.read_database("SELECT * FROM tags", conn, infer_schema_length=None)
            if t.height:
                tags.append(t)
        finally:
            conn.close()
    if tags:
        pl.concat(tags, how="vertical_relaxed").unique(subset=["tag_id"], keep="last").write_parquet(
            paths.journal / "tags.parquet", compression="zstd"
        )
    make_views(paths)
    return {"dbs": len(dbs), "events": events.height, "parquet": str(target)}


def make_views(paths: Paths | None = None) -> Path:
    paths = paths or Paths()
    db = paths.journal / "journal.duckdb"
    con = duckdb.connect(str(db))
    try:
        con.execute(
            f"CREATE OR REPLACE VIEW events AS SELECT * FROM read_parquet('{paths.journal / 'events.parquet'}')"
        )
        if (paths.journal / "tags.parquet").exists():
            con.execute(f"CREATE OR REPLACE VIEW tags AS SELECT * FROM read_parquet('{paths.journal / 'tags.parquet'}')")
        con.execute(
            "CREATE OR REPLACE VIEW trades AS SELECT *, json_extract_string(payload, '$.trade_id') AS trade_id,"
            " json_extract_string(payload, '$.symbol') AS trade_symbol,"
            " CAST(json_extract_string(payload, '$.net_r') AS DOUBLE) AS net_r,"
            " CAST(json_extract_string(payload, '$.net_pnl') AS DOUBLE) AS net_pnl"
            " FROM events WHERE event_type='TRADE'"
        )
        con.execute(
            "CREATE OR REPLACE VIEW signals AS SELECT *, json_extract_string(payload, '$.reject_reason') AS reject_reason,"
            " CAST(json_extract_string(payload, '$.score') AS DOUBLE) AS score"
            " FROM events WHERE event_type='SIGNAL'"
        )
    finally:
        con.close()
    return db


def query(sql: str, paths: Paths | None = None) -> pl.DataFrame:
    paths = paths or Paths()
    db = paths.journal / "journal.duckdb"
    if not db.exists():
        make_views(paths)
    con = duckdb.connect(str(db), read_only=True)
    try:
        return pl.from_pandas(con.execute(sql).fetchdf())
    finally:
        con.close()
