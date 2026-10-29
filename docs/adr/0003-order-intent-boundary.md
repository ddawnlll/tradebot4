# ADR 0003: OrderIntent is the only execution boundary

Status: accepted

## Context

If strategies submitted orders directly, backtest and live would diverge and
risk checks could be bypassed.

## Decision

The core produces immutable `OrderIntent` values containing signal id, symbol,
side, action, entry type/price, stop, take profits, quantity, risk in USDT,
legs and reason. Nothing in `core/` can submit, modify or cancel an order.

The Nautilus bridge and the future Binance adapter are the only translators.
Protective orders are created by the adapter from a `PROTECT` intent; the
engine never assumes an order exists.

## Consequences

* Risk and sizing are enforced once, before the adapter.
* Order lifecycle bugs are adapter bugs and are tested with synthetic
  scenarios (entry, TP1, TP2, stop, partial fills, stop replacement).
* The core can be unit-tested with no broker at all.
