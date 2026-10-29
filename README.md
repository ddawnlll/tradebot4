# tradebot

Range mean-reversion system for Binance USDT-M perpetual futures, built for a
20 USDT account.

**NautilusTrader is used only as a historical backtester** (`nautilus_trader==2.0.0rc8`).
All market intelligence, strategy logic, risk, journaling and live execution
are framework-independent Python inside `src/bot/core` and the adapters around
it.

> Status: research and validation. No live execution adapter exists. The
> validation gate (Milestone 6) decides GO / NO_GO; live code is only written
> after a GO and human approval.

## Layout

```
src/bot/core        framework-independent trading intelligence (no nautilus, no binance, no streamlit)
src/bot/data        Binance data acquisition, canonical parquet, Nautilus catalog export
src/bot/backtest    Nautilus v2 adapter (bridge, runner, fill models, reports, validation)
src/bot/journal     Append-only SQLite WAL event journal + parquet export + tagging
dashboard/          Streamlit UI (7 pages)
docs/               architecture, Nautilus notes, exchange constraints, assumptions, runbook, ADRs
```

Dependency direction is enforced by `tests/unit/test_dependency_rules.py`.

## Quick start

```bash
uv sync --frozen
uv run bot --help
uv run pytest -q
```

## Reproduce the research

```bash
uv run bot data instruments
uv run bot data universe --as-of 2024-11-01 --top-n 40
uv run bot data sync --symbols universe --start 2024-11-01 --end 2026-10-20
uv run bot data normalize
uv run bot data quality --start 2024-11-01 --end 2026-10-20

uv run bot backtest --strategy range_v1 --symbols universe --start 2024-11-01 --end 2025-12-31
uv run bot validate --symbols universe --start 2024-11-01 --dev-end 2025-12-31 --oos-end 2026-06-30 --vault-end 2026-10-20

uv run bot journal etl
uv run bot dashboard
```

Every run writes `runs/<timestamp>_<strategy>_<config_hash>/` with metadata,
config, metrics, trades, fills, orders, equity, signals, tags, ambiguity audit,
journal and enrichment. Validation writes `reports/validation_<timestamp>/`.

## Design in one paragraph

Binance 1m klines are normalized to canonical parquet. A framework-independent
core aggregates them to 15m/1h/4h, confirms pivots with an explicit delay,
classifies the regime with ADX and Choppiness, validates a support/resistance
range, and asks the strategy for an entry only at the range edge with
rejection. Sizing accounts for fees, slippage, exchange minimums, leverage and
liquidation distance. The engine emits `OrderIntent`; the Nautilus bridge
submits a market entry, a native stop and one or two take-profit limits, and
records every event in an append-only journal. After TP1 the stop moves to
breakeven plus costs on the next bar.

## Documents

- `docs/architecture.md` - components, invariants, failure policy
- `docs/nautilus_v2.md` - pinned version, verified API surface, upgrade policy
- `docs/nautilus_gaps.md` - what the pinned release does *not* provide
- `docs/exchange_gaps.md` - Binance constraints for a 20 USDT account
- `docs/backtest_assumptions.md` - fill, fee, funding and latency assumptions
- `docs/runbook.md` - operating the system
- `docs/adr/` - architecture decision records
- `docs/validation.md` - the GO/NO_GO report (generated per validation run)
