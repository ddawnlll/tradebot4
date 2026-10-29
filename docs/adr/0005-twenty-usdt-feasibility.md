# ADR 0005: 20 USDT feasibility gates every order

Status: accepted

## Context

With 20 USDT and 10x leverage the maximum notional is 200 USDT. Binance
minimum notional is 5 USDT for most perpetuals, 20 for a few majors and 50 for
BTC. Fees are 0.02%/0.05% and funding is charged every 4-8 hours. A strategy
that ignores these constraints can look profitable in a frictionless backtest
and be untradeable live.

## Decision

Before any entry:

1. size from the risk budget including round-trip taker fees and 2 bps/side
   slippage,
2. round quantity down to the step and reject below minimum quantity,
3. reject below `1.10 x MIN_NOTIONAL`,
4. reject if notional exceeds equity x max leverage,
5. require margin with a 20% buffer,
6. require the stop to sit before liquidation with a 0.5% buffer,
7. reject orders above 2% of the signal bar's volume,
8. if two take-profit legs cannot each satisfy the exchange minimum, fall back
   to a single take profit and record that in the trade features.

## Consequences

* Some symbols (notably BTC at 50 USDT minimum) are frequently untradeable.
* The strategy's trade count is supply-limited by the account size, not only
  by signal quality.
* `MIN_NOTIONAL` and `MIN_QUANTITY` rejections are first-class journal events,
  not silent skips.
