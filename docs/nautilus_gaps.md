# NautilusTrader v2.0.0rc8 gaps and workarounds

Only gaps that this project actually hit are listed. Each has a workaround and
a test or a documented limitation.

| # | Gap | Impact | Workaround | Status |
|---|---|---|---|---|
| 1 | `PositionAdjusted` (funding/commission adjustments) is dropped before the Python strategy callback (`crates/trading/src/strategy/mod.rs`: `PositionEvent::PositionAdjusted(_) => return`) | Strategies cannot observe funding charges | Funding is reconstructed analytically from the fill timeline and the Binance funding series, then added to each trade; account-vs-trades residual is written to `metrics.json` and is zero in tests | worked around |
| 2 | `ParquetDataCatalog` has no funding-rate writer or query in this release | Funding cannot travel through the catalog | Funding is fed directly with `BacktestEngine.add_data([FundingRateUpdate...])`; the `BacktestNode` path is used only for golden tests without funding | worked around |
| 3 | BacktestDataConfig requires `InstrumentId` and `BarSpecification` objects; strings raise TypeError | Config construction is not string-friendly | Construct typed objects explicitly in `runner.run_backtest_node` | worked around |
| 4 | `ParquetDataCatalog` refuses to create a missing directory | First export fails on a fresh checkout | `to_nautilus.export_symbol` creates the directory first | worked around |
| 5 | Bar volume precision mismatch causes the bar to be silently skipped for matching | A backtest can produce no fills while still dispatching bars to the strategy | Always build volume with `instrument.make_qty`; the runner does this | worked around |
| 6 | `StrategyConfig` subclassing is fragile: `@dataclass` breaks `super()`, extra constructor kwargs are rejected by `Strategy.__new__` | Dependency injection into a strategy is awkward | Keyword-only `__init__`, no dataclass, attach dependencies to the config instance | worked around |
| 7 | Bar execution uses one fixed intrabar path per bar (`Open->High->Low->Close`, or the adaptive variant). It cannot express "stop first" | A stop and a TP inside one bar have an ambiguous fill order | 1m execution bars make this rare; ambiguity count is reported; hypothetical outcomes use STOP_FIRST | documented limitation |
| 8 | No queue-position or latency model for bar data beyond `StaticLatencyModel`; a latency model delays settlement to the next bar close | Adding realistic latency makes bar fills one full bar late | Latency is set to zero and a per-side slippage allowance is included in sizing; execution-quality gap is reported | documented limitation |
| 9 | The catalog does not ship a custom-data writer that round-trips arbitrary payloads with a stable schema | Storing our signal records in the catalog is not attractive | Signals live in the journal, not the catalog | accepted |
| 10 | `BacktestNode` builds engines internally, so a custom portfolio statistic cannot be registered before the run | Custom stats need another path | Reports are computed from trades and equity outside Nautilus | accepted |

## Not a gap but worth recording

* `BacktestEngine.add_data()` copies its input; clearing the caller's list does
  not affect the engine.
* `BacktestEngine.reset()` keeps instruments and data but clears orders,
  positions and account state.
* A market order can fill in more than one slice on bar data; the bridge keeps
  the client-order mapping until the order is fully filled.
