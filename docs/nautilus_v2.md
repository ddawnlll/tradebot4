# NautilusTrader v2: pinned version and verified API

## Version

| Item | Value |
|---|---|
| Package | `nautilus_trader==2.0.0rc8` (exact pin) |
| PyPI check | 2.0.0rc8 is the newest published v2 on 2026-10-29 (1.231.0 is v1) |
| Wheel | `cp313` macOS arm64 (also cp314) |
| Python | 3.13.9 |
| Lock | `uv.lock` |
| Git tag inspected | `v2.0.0rc8` (commit `d53849b216e2`) |

`uv run python -c "import nautilus_trader; print(nautilus_trader.__version__)"`
prints `2.0.0rc8`.

## Import layout (v2 differs from v1)

Everything is Rust-native and exposed from the compiled extension:

```python
from nautilus_trader.backtest import BacktestEngine, BacktestNode
from nautilus_trader.config import BacktestEngineConfig, RiskEngineConfig, LoggerConfig
from nautilus_trader.execution import MakerTakerFeeModel, DefaultFillModel
from nautilus_trader.model import CryptoPerpetual, Bar, BarType, BarSpecification
from nautilus_trader.persistence import ParquetDataCatalog
from nautilus_trader.trading import Strategy
```

There is no `nautilus_trader.model.instruments` or
`nautilus_trader.persistence.catalog` submodule in this release.

## Verified behaviours (all exercised by this repository)

| Behaviour | Evidence |
|---|---|
| Bar execution requires `bar_execution=True`, `BookType.L1_MBP`, external time bars | `bar-execution.md` in the tagged docs; runner config |
| `ts_init` must be the bar close | docs; our bars set `ts_event = ts_init = close` |
| Bar volume precision must equal the instrument's size precision, otherwise the bar is skipped | observed warning and fix |
| Min notional is enforced by the risk engine (`NOTIONAL_BELOW_MINIMUM`) | observed denial |
| Taker/maker fees are taken from the venue fee model | fills and account reconciliation |
| Funding settles from `FundingRateUpdate` at `next_funding_ns`; positive rate debits longs | synthetic funding test, account balance |
| A market order on bars can produce multiple fills | partial-fill lifecycle test |
| Reduce-only orders are rejected when the position is flat | observed rejection |
| `PositionAdjusted` is **not** delivered to Python strategies (explicit early return) | Rust source `crates/trading/src/strategy/mod.rs` |
| The catalog has no funding writer/query | stub inspection |
| `BacktestNode` needs `InstrumentId` and `BarSpecification` objects (not strings) | observed TypeErrors |
| `ParquetDataCatalog` requires the directory to exist | observed OSError |
| `BacktestNode.add_strategy(run_config_id, strategy_instance)` accepts an instance | golden test |

## Strategy config rules in v2

`StrategyConfig` is a Rust/PyO3 type. A Python subclass must:

* declare keyword-only `__init__` arguments,
* accept and ignore extra keyword arguments,
* call `super().__init__()` with no arguments,
* **not** be decorated with `@dataclass` (the decorator breaks zero-arg
  `super()`),
* not pass extra positional arguments to the strategy constructor; attach
  dependencies to the config instance instead and read them in `__init__`.

`Strategy.__new__` rejects unknown keyword arguments, so dependency injection
is done by setting attributes on the config object before constructing the
strategy.

## Upgrade policy

Upgrades are never automatic.

1. separate branch `chore/nautilus-v2-upgrade`,
2. read the release notes and diff the tagged source,
3. run the API smoke tests and the golden BacktestNode test,
4. compare golden trades, fills and net PnL against the pinned release,
5. benchmark runtime,
6. separate PR with rollback notes.

v1 is not a fallback. If a required v2 API is missing, record it in
`docs/nautilus_gaps.md` and implement the workaround outside Nautilus.
