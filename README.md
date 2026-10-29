# tradebot

Range mean-reversion system for Binance USDT-M perpetual futures, built for a 20 USDT account.

**NautilusTrader is used only as a historical backtester** (`nautilus_trader==2.0.0rc8`).
All market intelligence, strategy logic, risk, journaling and live execution are framework-independent
Python inside `src/bot/core` and the adapters around it.

## Layout

```
src/bot/core        framework-independent trading intelligence (no nautilus, no binance, no streamlit)
src/bot/data        Binance data acquisition, canonical parquet, Nautilus catalog export
src/bot/backtest    Nautilus v2 adapter (bridge, runner, fill models, reports, validation)
src/bot/execution   Binance live/testnet adapter (independent of Nautilus)
src/bot/journal     Append-only SQLite WAL event journal + parquet export
src/bot/kaizen      Weekly reports, calibration, hypotheses, promotion gates
dashboard/          Streamlit UI
```

Dependency direction is enforced by `tests/unit/test_dependency_rules.py`.

## Quick start

```bash
uv sync --frozen
uv run bot --help
uv run pytest -q
```

## Docs

- `docs/architecture.md` - component map and invariants
- `docs/nautilus_v2.md` - pinned version, verified API surface, upgrade policy
- `docs/nautilus_gaps.md` - what the pinned release does *not* provide
- `docs/exchange_gaps.md` - Binance-side constraints for a 20 USDT account
- `docs/backtest_assumptions.md` - fill/fee/funding/latency assumptions
- `docs/runbook.md` - operating the bot
- `docs/adr/` - architecture decision records
