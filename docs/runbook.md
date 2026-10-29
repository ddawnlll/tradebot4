# Runbook

## Environment

```bash
uv python pin 3.13
uv sync --frozen
uv run python -c "import nautilus_trader; print(nautilus_trader.__version__)"   # 2.0.0rc8
uv run pytest -q
```

## Data

```bash
uv run bot data instruments
uv run bot data universe --as-of 2024-11-01 --top-n 40
uv run bot data sync --symbols universe --start 2024-11-01 --end 2026-10-20
uv run bot data normalize
uv run bot data quality --start 2024-11-01 --end 2026-10-20
uv run bot data catalog --symbols ETHUSDT --start 2024-11-01 --end 2024-12-01
```

Raw ZIPs live in `data/raw`, canonical parquet in `data/market`, and the
Nautilus catalog in `data/nautilus_catalog` (derived only).

## Research

```bash
uv run bot scan --days 30
uv run bot backtest --strategy range_v1 --symbols universe --start 2024-11-01 --end 2025-12-31
uv run bot validate --symbols universe --start 2024-11-01 --dev-end 2025-12-31 --oos-end 2026-06-30 --vault-end 2026-10-20
```

Each run writes `runs/<timestamp>_<strategy>_<config_hash>/` with config,
metadata, metrics, trades, fills, orders, equity, signals, tags, journal and
charts. The validation report is written to `reports/validation_<timestamp>/`.

## Journal

```bash
uv run bot journal etl
uv run bot journal enrich --run runs/<run_id>
uv run bot dashboard
```

## Daily / weekly operation (only after GO)

1. Check `bot status`.
2. Check the latest journal for `order_rejected`, `missing_stop` and
   reconciliation residuals. Any non-zero residual stops new entries.
3. Confirm every open position has a native protective order at the exchange.
4. Review the previous day's trades, tags and missed opportunities.
5. Run the weekly kaizen report once implemented.

## Incident procedures

| Event | Action |
|---|---|
| Exchange disconnected | Do not open new positions. Existing native stops remain. Reconnect and reconcile. |
| Missing protective stop | Flatten the position at market, then halt new entries and investigate. |
| Reconciliation mismatch | Halt (`REDUCING`), reconcile against exchange state, require human approval to resume. |
| Kill switch | HALTED. Positions are flattened; resuming requires human approval. |
| Data stale | No new entries. Cancel resting entry orders. |
| API key compromise | Revoke the key, flatten via the exchange UI, rotate secrets. |

## Secrets and account security

* API keys only in the environment or a systemd `EnvironmentFile`, never in
  git.
* Withdrawals disabled; futures trading permission only; IP allowlist.
* Testnet first, then a tiny live balance only after a written GO.

## What NOT to do

* Do not change parameters on a live process.
* Do not widen a stop.
* Do not average down.
* Do not run more than one bot process against the same account.
* Do not open the vault period during development.
