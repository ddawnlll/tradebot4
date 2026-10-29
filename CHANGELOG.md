# Changelog

## 0.1.0 - 2026-10-29

Research system, first complete pass through Milestone 6.

### Added

- Framework-independent core: UTC bar aggregation (1m -> 15m/1h/4h), Wilder
  ATR, confirmed pivots with R-bar delay, ATR zone clustering, sweep buffer,
  ADX + Choppiness regime, range scoring, scanner, decision engine.
- Range V1 strategy plus trend-pullback and seeded random-entry benchmarks.
- Fee/slippage-aware position sizing with exchange minimums, leverage, margin
  and liquidation checks; account risk policy with daily loss limit, losing
  streak cooldown and drawdown kill switch.
- Binance public data downloader (checksum verified, resumable), canonical
  parquet normalizer, point-in-time frozen universe, instrument metadata, data
  quality report, Nautilus catalog export.
- NautilusTrader 2.0.0rc8 backtest adapter: low-level engine runner and a
  catalog-driven `BacktestNode` path with a golden regression test.
- Funding accounting reconstructed from the fill timeline because the pinned
  release drops `PositionAdjusted` before Python; account-vs-trades residual is
  reported per run.
- Append-only SQLite journal, DuckDB/Parquet ETL, rule-based error tags,
  post-exit drift, missed-opportunity analysis, ambiguity audit.
- Validation suite: walk-forward, parameter sensitivity, ablations, benchmark
  strategies, Monte Carlo (iid and block bootstrap), bootstrap confidence
  intervals, PSR/DSR and a pre-registered GO/NO_GO gate.
- Streamlit dashboard with seven pages and unit tests.
- Docs: architecture, Nautilus v2 notes and gaps, exchange constraints,
  backtest assumptions, runbook, five ADRs.

### Known limitations

- Live execution is not implemented (gated on GO).
- Bar execution cannot resolve intrabar stop-vs-target ordering; ambiguity is
  counted and reported.
- Backtest stops trigger on `LAST`, live must use `MARK_PRICE` and be validated
  on testnet.
- No latency model; bar-only latency would defer fills by a full bar.
