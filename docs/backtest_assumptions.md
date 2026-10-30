# Backtest assumptions

Every assumption below is either implemented in code or explicitly listed as a
known divergence. The goal is a result that is wrong in a *conservative*
direction, or clearly labelled as optimistic.

## Data and time

| Item | Assumption |
|---|---|
| Base data | Binance `data.binance.vision` 1m klines, checksum verified |
| Timestamps | UTC epoch ms; `ts_close_ms` is exclusive (`close_time + 1`) |
| Nautilus timestamps | `ts_event = ts_init = ts_close_ms * 1e6`; bar execution requires the close |
| Decision bars | 15m entry, 1h context, 4h higher-timeframe bias, built by the core aggregator |
| Incomplete bars | A 15m bar with a gap or fewer than 15 base bars is marked incomplete and never traded |
| Look-ahead | Pivots are confirmed after `R` right bars and never revised; decisions use only closed bars |
| Warmup | 1h regime needs 40 bars, 15m ATR needs 14, zones need several pivots; entries are blocked until ready |

## Execution

| Item | Assumption | Bias |
|---|---|---|
| Entry | Market order at the 15m close that produced the signal (no latency model), filled 2 bps or one tick adverse, whichever is larger | optimistic on timing, conservative on price |
| Exit (signal) | Market order at the 1m close where the rule triggered, 2 bps or one tick adverse | conservative on price |
| Stop | `STOP_MARKET`, trigger on `LAST`, fills at the next synthetic price point with an additional 5 bps adverse; bar gaps are included | conservative |
| Take profit | `LIMIT` fills only after the market trades at least one tick through the limit, with a 90% touch-fill probability; maker fee | conservative |
| Intrabar path | `Open -> High -> Low -> Close` (`bar_adaptive_high_low_ordering=False`) | optimistic for longs whose TP precedes the stop, conservative for shorts |
| Ambiguity | A bar where both stop and TP are touched is counted and reported; hypothetical analyses use `STOP_FIRST` | reported |
| Latency | Zero. A `StaticLatencyModel` on bar-only data defers fills to the next bar close, which overstates delay | neutral for research, must be revisited live |
| Participation | Orders above 2% of the signal bar's volume are rejected | conservative |
| Partial fills | Handled: order mapping is kept until the order is fully filled; protection is resized | realistic |

## Costs

| Cost | Value |
|---|---|
| Maker fee | 0.020% |
| Taker fee | 0.050% |
| Slippage in sizing | 2 bps per side, included in the risk budget |
| Market fill slippage | `max(2 bps, 1 tick)` adverse |
| Stop fill slippage | 5 bps adverse plus any bar gap |
| Limit fill rule | at least one tick through the limit; 90% touch fill probability |
| Funding | Historical rate at publication time; positive rate debits longs; missing or gappy funding aborts the run |
| Funding price proxy | 1m close at settlement, not mark price (mark data not in the catalog) |
| Fee reserve | 1.0x the taker fee at entry and exit |

## Capital and risk

| Item | Value |
|---|---|
| Starting equity | 20 USDT |
| Risk per trade | 5% of current equity (configurable 2-10%) |
| Max leverage | 10x |
| Max positions | 1 (config can raise to 2) |
| Margin buffer | 20% of free margin left unused |
| Min notional buffer | 10% above the exchange minimum |
| Liquidation | Isolated-margin approximation with 0.5% maintenance; the stop must sit before liquidation with a 0.5% buffer |
| Daily loss limit | 15% -> `REDUCING` (no new entries) |
| Losing streak | 4 losses -> 24h cooldown |
| Kill switch | 50% drawdown -> `HALTED`, human approval required to resume |

## Accounting and reconciliation

The Nautilus account is authoritative for equity. The core records gross PnL,
fees, funding and slippage per trade. Funding is reconstructed from the fill
timeline because the pinned release drops `PositionAdjusted` before Python.
After every run:

```
residual = final_equity - starting_balance - sum(trade.net_pnl)
```

A residual above 0.5% of starting equity is logged; the current test suite and
development runs reconcile to 0.0 USDT.

## Not modelled

* Mark-price triggered stops and exchange liquidation-engine details.
* Order-book depth, queue position and market impact.
* Exchange outages, API rate limits, listen-key expiry, partial exchange
  rejects beyond the Nautilus risk engine.
* Funding-rate prediction between the last data point and the next settlement.
* Taxes, withdrawal limits, and jurisdiction restrictions.

These are reasons why a GO is *permission to test on testnet*, not permission
to trade real money.

## Reconciliation tolerance

The equity change must equal the sum of trade net PnL up to a small residual.
The residual comes from funding events that occur while the account is flat:
the venue debits or credits the account, but there is no open trade to attach
the amount to. The accepted tolerance is `max(0.01 USDT, 0.5% of the starting
balance)`, i.e. 0.10 USDT at the 20 USDT research size. The cost test asserts
exact reconciliation (`< 1e-6`) when funding is disabled; the funding-enabled
test asserts the documented tolerance. A residual above the tolerance is a
warning in `runner.py` and must be investigated, not ignored.
