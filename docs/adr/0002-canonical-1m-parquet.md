# ADR 0002: 1m parquet is canonical; Nautilus catalog is derived

Status: accepted

## Context

Nautilus requires its own catalog format and has release-specific schema
details. Keeping research data inside it would couple the dataset to a
release candidate and make live analysis depend on Nautilus.

## Decision

* Canonical data is 1m OHLCV parquet partitioned by symbol/year/month, UTC
  timestamps, close time exclusive.
* Higher timeframes are built incrementally by the core aggregator in both
  backtest and live.
* The Nautilus catalog is generated from canonical parquet and used only for
  `BacktestNode` and golden tests.
* Funding rates are stored separately and fed to the engine directly because
  the pinned catalog has no funding writer.

## Consequences

* Data can be analysed with Polars/DuckDB and used by the dashboard without
  Nautilus.
* A catalog export step is required for node runs.
* The 15m/1h/4h bars are guaranteed identical between backtest and live.
