"""Append-only journal schema.

One event table holds every decision and execution fact from every source
(backtest, shadow, testnet, live). Payloads are JSON so the schema can evolve
without losing history; the envelope columns are stable and indexed.

Price series are deliberately *not* stored here. They live in the canonical
parquet market store and are joined by symbol/timestamp at analysis time.
"""

from __future__ import annotations

SCHEMA_VERSION = 1

DDL = """
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;

CREATE TABLE IF NOT EXISTS events (
    event_id        TEXT PRIMARY KEY,
    schema_version  INTEGER NOT NULL,
    source          TEXT NOT NULL,
    event_type      TEXT NOT NULL,
    ts_event_ms     INTEGER NOT NULL,
    ts_recorded_ms  INTEGER NOT NULL,
    symbol          TEXT,
    signal_id       TEXT,
    strategy_version TEXT,
    config_hash     TEXT,
    run_id          TEXT NOT NULL,
    payload         TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_events_run ON events(run_id, event_type);
CREATE INDEX IF NOT EXISTS idx_events_symbol_ts ON events(symbol, ts_event_ms);
CREATE INDEX IF NOT EXISTS idx_events_signal ON events(signal_id);

CREATE TABLE IF NOT EXISTS runs (
    run_id          TEXT PRIMARY KEY,
    created_ms      INTEGER NOT NULL,
    source          TEXT NOT NULL,
    strategy        TEXT NOT NULL,
    strategy_version TEXT NOT NULL,
    config_hash     TEXT NOT NULL,
    nautilus_version TEXT,
    python_version  TEXT,
    data_hash       TEXT,
    seed            INTEGER,
    designation     TEXT,           -- research | oos | vault
    metadata        TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tags (
    tag_id          TEXT PRIMARY KEY,
    trade_id        TEXT NOT NULL,
    tag             TEXT NOT NULL,
    source          TEXT NOT NULL,  -- rule | human
    rule_version    TEXT,
    notes           TEXT,
    ts_ms           INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_tags_trade ON tags(trade_id);
"""


def ddl() -> str:
    return DDL
