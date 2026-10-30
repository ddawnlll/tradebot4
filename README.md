# tradebot4

Range mean-reversion research system for Binance USDT-M perpetual futures,
built around a 20 USDT account. NautilusTrader is used **only as a historical
backtester** (`nautilus_trader==2.0.0rc8`); the market intelligence, decision
engine, risk, journal and research tooling are framework-independent Python.

**Status: research / validation. NO_GO on every tested family. No live or
testnet execution adapter exists. No API keys are required.**

- Public repository: <https://github.com/ddawnlll/tradebot4>
- Last verified: 2026-10-30 on macOS arm64, Python 3.13.9, uv 0.9.14.
- Test suite: **154 passed**, ruff **clean**.
- Sealed holdout: **2026-07-01 .. 2026-10-20**, not opened.

## Test report (2026-10-30)

Run from the repository root:

```bash
uv sync --frozen
uv run pytest tests -q
uv run ruff check src tests dashboard scripts
```

| Check | Result |
|---|---|
| `pytest` (market data installed) | **154 passed** in 6.7 s |
| `pytest` (fresh clone, no data) | **151 passed, 3 skipped** with an explicit `market data ... not installed` reason |
| `ruff check src tests dashboard scripts` | **All checks passed** |
| Python | 3.13.9 (uv virtual environment) |
| NautilusTrader | 2.0.0rc8 |
| Platform | macOS arm64 (darwin) |
| Data-dependent tests | use synthetic fixtures or small recorded ranges; no live network |

The suite covers: 1m→15m/1h/4h aggregation, confirmed pivots with delay, range
scoring, decision/risk sizing, fee/slippage/funding accounting, Nautilus bridge
lifecycle, cost model, funding coverage, reconciliation tolerance, partition
exports, audit identity/atomic history, passive limit entries, 1h entry
configuration, observational MarketState features, capital viability, exit
scanning and static-target exits.

The data-dependent tests skip with an explicit reason when the 2.7 GB market
data is absent; run `bot data sync` to install it and get the full 154-test run.
Live/testnet is intentionally absent.

## Research status

| Round | Result | Key numbers |
|---|---|---|
| Phase 1 validation | **NO_GO** | 240 dev trades, -7.54 USDT, PF 0.902; 82 old-OOS trades, -9.81, PF 0.62 |
| Costed baseline (ADR 0007) | **NO_GO** | 103 dev-A trades, net -7.85, PF 0.767, 50.4% DD; kill switch 2025-01-19 |
| 4-fold baseline | **NO_GO** | 253 trades, net -34.77, PF 0.625, -0.1697R, 0/4 positive |
| H1 passive entry | **NO_GO** | best 678 trades, PF 0.919, -0.0296R, stress 0.860 |
| H2 stricter width/cost | **NO_GO** | ratio 4.0 leaves 27 trades; 5.0+ leaves none |
| H3 1h signals | **NO_GO** | 89 trades, PF 0.884, -0.0391R, stress 0.795 |
| V8-core transfer | **NO_GO** | pure static 2R 133 trades, PF 0.625, -0.3017R; random same-exit 2R PF 0.979, -0.0020R |

The costed baseline audit: gross before costs -0.0022 USDT/trade, total cost
0.0741/trade, BREAKOUT exits 90.8% of negative PnL, long -7.74 vs short -0.12.
The V8 transfer showed that range entries with a 2R exit are much worse than
random entries with the same exit; the entry mechanism, not the exit alone, is
the weak link. See the reports below.

**Holdout policy:** the only holdout is 2026-07-01..2026-10-20 (manifest
`4768406fa559fc4fc0f2124fc946a262`). It was not opened. 2026-01..2026-06 is
development data, not fresh OOS. Do not open the holdout to tune a candidate.

## Use on another computer

Prerequisites:

- Python 3.13 or 3.14 (`uv` will create the environment)
- [uv](https://docs.astral.sh/uv/)
- git
- ~3 GB free disk for the 1m market data; the repository itself is ~2 MB

```bash
git clone https://github.com/ddawnlll/tradebot4.git
cd tradebot4

uv sync --frozen
uv run bot --help
uv run pytest tests -q
uv run ruff check src tests dashboard scripts
```

No API key is needed for backtests. Do not create or commit `.env`; public
Binance market data is downloaded with unauthenticated REST endpoints.

### Data (not included in git)

The 1m klines, funding, runs, journals and dashboards are intentionally
gitignored. On a new machine:

```bash
uv run bot data instruments
uv run bot data universe --as-of 2024-11-01 --top-n 40
uv run bot data sync --symbols universe --start 2024-11-01 --end 2026-10-20
uv run bot data normalize
uv run bot data quality --start 2024-11-01 --end 2026-10-20
```

Then reproduce the baseline:

```bash
uv run bot validate --symbols universe --start 2024-11-01 \
  --dev-end 2025-12-31 --oos-end 2026-06-30 \
  --vault-end 2026-10-20 --workers 4
```

`runs/` and `logs/` are gitignored. Every run writes a self-describing
directory with `metadata.json`, `config.yaml`, `metrics.json`, trades, fills,
orders, equity and partitioned signals.

### Research commands

```bash
uv run python scripts/exit_scan.py
uv run python scripts/exit_replay.py
uv run python scripts/feature_ablation.py
uv run python scripts/chop_scan.py
uv run python scripts/capital_viability.py
uv run python scripts/audit_why_no_edge.py
```

## Tracked reports

Only curated text/JSON reports are tracked; raw `runs/`, `data/` and logs are
not. Start here:

- `reports/research/v8_transfer/report.md` - V8-core transfer: exit, features,
  chop, capital, expert decision
- `reports/research/v8_transfer/exit_replay.md` - pure static 2R/3R full replay
- `reports/research/v8_transfer/exit_scan.md` - conditional 15-arm exit scan
- `reports/research/v8_transfer/feature_ablation.md` - observation-only feature
  separation
- `reports/research/v8_transfer/chop_scan.md` - A2/A3/A5 diagnostic
- `reports/research/v8_transfer/capital_viability.json` - 20/500 USDT envelope
- `reports/baseline_costed/baseline.md` - costed baseline and stress
- `reports/research_round1/report.md` - H1/H2/H3 round
- `docs/audits/why_no_edge.md` - living loss audit
- `docs/research/v8_transfer_inventory.md` - V8 idea inventory with file:line
- `docs/research/v8_expert_assessment.md` - five experts; next candidate
- `docs/validation.md` - Phase 1 NO_GO report
- `kaizen/hypotheses/` - preregistered hypotheses and results
- `kaizen/experiments.parquet` - append-only experiment ledger
- `kaizen/audit_history.parquet` - append-only loss-audit history

## Layout

```text
src/bot/core        framework-independent market intelligence and decision engine
src/bot/data        Binance data acquisition, canonical parquet, catalog export
src/bot/backtest    Nautilus v2 adapter, cost model, validation, export
src/bot/journal     append-only SQLite WAL journal + parquet/duckdb
src/bot/audit       continuous loss audit
src/bot/research    conditional exit/chop diagnostics
src/bot/risk        sizing, policy, capital viability
dashboard/          Streamlit UI (7 pages)
docs/               architecture, Nautilus notes, assumptions, ADRs, audits
kaizen/             hypotheses, experiment ledger, audit history
reports/            curated research reports (tracked); raw runs are ignored
scripts/            research/reproduction entry points
```

## Safety

- Backtest only; no live/testnet order path, no API keys, no credentials.
- Market data is public Binance USDT-M data; no private endpoints.
- The holdout is sealed; do not use it to choose or tune a candidate.
- A positive development result is at most `INCONCLUSIVE / GO-candidate`
  until the frozen gates and a single holdout opening are completed.

## License

No license file is included. Add one before redistribution or external use.
