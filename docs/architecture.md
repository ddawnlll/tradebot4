# Architecture

The system separates **trading intelligence** from **execution technology**. The
same decision code is used for backtest, shadow, testnet and live; only the
adapter changes.

```
                BINANCE PUBLIC DATA (data.binance.vision, fapi)
                                |
                                v
                          data layer (parquet)
                                |
                                v
                         core (framework free)
        aggregation -> zones/regime -> scoring -> strategy -> risk
                                |
                 OrderIntent (side, stop, TPs, qty, risk)
                    /                            \
                   v                              v
        NautilusTrader v2 backtester        Binance execution adapter
        (bars, fills, fees, funding)       (not built until GO)
                    \                            /
                     v                          v
                        append-only journal (SQLite WAL)
                                |
                     parquet export / DuckDB views
                                |
                     dashboard + kaizen loop
```

## Hard invariants

1. `src/bot/core/**` imports no `nautilus_trader`, Binance SDK, Streamlit,
   DuckDB or web framework. Enforced by `tests/unit/test_dependency_rules.py`.
2. `nautilus_trader` is imported only under `src/bot/backtest/` and
   `src/bot/data/to_nautilus.py`.
3. The core never submits orders. It returns `OrderIntent` values. Adapters
   translate them.
4. Strategies never size positions. Sizing is a separate risk module that sees
   exchange filters, fees, leverage and liquidation distance.
5. A decision uses only bars whose `ts_close_ms` has been reached. Pivots are
   confirmed with an explicit right-window delay and never revised.
6. The journal is append-only. Corrections are new rows (tags), never updates.
7. Backtest and live use the same 1m -> 15m/1h/4h aggregator, so a signal at
   09:30:00 UTC sees the same bars in both.

## Components

| Component | Module | Responsibility |
|---|---|---|
| Aggregation | `core/aggregation.py` | UTC-aligned 1m -> 15m/1h/4h, completeness, gaps |
| ATR | `core/indicators/atr.py` | Wilder ATR, EMA, rolling std |
| Zones | `core/indicators/zones.py` | confirmed pivots, ATR clustering, sweep buffer |
| Regime | `core/indicators/regime.py` | ADX + Choppiness, squeeze and breakout risk |
| State | `core/market/state.py` | per-symbol feature state, 24h liquidity |
| Scoring | `core/market/scoring.py` | range validation and normalized score |
| Scanner | `core/market/scanner.py` | cross-sectional liquidity ranking |
| Strategies | `core/strategies/` | range V1, trend-pullback and random benchmarks |
| Sizing | `core/risk/sizing.py` | fee/slippage-aware risk sizing, legs, liquidation |
| Policy | `core/risk/policy.py` | daily loss, losing streak, kill switch |
| Engine | `core/engine.py` | orchestration, position lifecycle, trade records |
| Backtest | `backtest/` | Nautilus bridge, runner, fill assumptions, reports |
| Data | `data/` | download, normalize, instruments, universe, quality, catalog |
| Journal | `journal/` | schema, recorder, ETL, tagging, enrichment, analysis |

## Data flow per bar

1. Nautilus dispatches a closed 1m bar to `BacktestBridge.on_bar`.
2. The bridge converts it to a core `Bar` and takes a portfolio snapshot every
   15 minutes.
3. `DecisionEngine.on_bar` updates `SymbolState` (aggregators, ATR, zones,
   regime, liquidity) and manages the open position.
4. On a completed 15m bar and only when flat, the strategy is asked for an
   entry. The scanner supplies the range setup.
5. Risk sizing produces a quantity or a reject reason. A reject is journaled
   (when a validated setup exists) and counted.
6. On approval the engine emits an `OrderIntent`. The bridge submits a market
   entry; on fill it submits a native stop and one or two take-profit limits.
7. After TP1 the stop is moved to breakeven plus costs on the **next bar**, not
   inside the fill callback. This avoids cancel/replace races when TP1 and TP2
   fill in the same bar.

## Why the stop move is deferred

The pinned Nautilus build processes all matching for a bar before dispatching
`on_order_filled`. Submitting a replacement stop inside the TP1 callback can
race a TP2 fill at the same timestamp, producing a reduce-only rejection
against a flat position. The original stop remains live until the next bar, so
the position is never unprotected; only the breakeven move is delayed by at
most one minute.

## Failure policy

* A rejected protective stop triggers an immediate flatten unless the broker
  position is already flat, in which case the core reconciles to flat.
* Any bridge error aborts the run (`strict_errors=True`) rather than producing
  a quietly incomplete result.
* Funding is reconciled analytically and the account-minus-trades residual is
  written to `metrics.json`; a non-zero residual above 0.5% of starting equity
  is logged.
