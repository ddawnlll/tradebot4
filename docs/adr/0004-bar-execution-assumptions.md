# ADR 0004: bar execution assumptions and ambiguity policy

Status: accepted

## Context

1m bars do not say whether the high or the low came first. If a stop and a
take profit are both inside one bar, the fill order is ambiguous. The pinned
Nautilus build offers only two deterministic paths: `O->H->L->C` and an
adaptive nearest-extreme variant.

## Decision

* Use 1m execution bars and `O->H->L->C`.
* Keep entries and exits at bar closes so the decision timestamp and the fill
  timestamp are the same event.
* Report the number of trades where both the stop and a take profit were
  inside the same 1m bar. Hypothetical analyses use `STOP_FIRST`.
* Do not claim that the backtest resolves intrabar ordering; treat the result
  as one sample from a plausible path.

## Consequences

* Longs are optimistic when TP and stop share a bar; shorts are conservative.
* 1m bars make the ambiguity rare, but not zero.
* Any live deployment must use native exchange stops and be validated on
  testnet, where real intrabar ordering exists.
