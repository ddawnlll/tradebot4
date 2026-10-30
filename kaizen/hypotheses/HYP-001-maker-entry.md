# HYP-001: Post-only limit entry at the range edge

Status: proposed (round 1)

## Evidence

The frozen baseline pays taker fees on entry and market slippage on every
entry. Its win rate is low and the average winner is small, so entry cost is a
large share of the gross edge. The current `RangeV1` entry is a market order at
the 15m close after a rejection: it always pays the spread and can enter late
in the move.

## Change (one change)

Add a passive entry mode: when flat and price is on the correct side of a
valid zone, place a post-only limit at the inner edge of the zone (long:
support upper edge, short: resistance lower edge), keep it for N bars, cancel
if unfilled. Stop, targets, sizing and the time stop are unchanged.

## Preregistered criteria

Using `config/gates.yaml` hypothesis criteria on development data with
walk-forward:

* at least 100 walk-forward trades,
* PF >= 1.10,
* PF >= 1.00 under cost stress,
* block-bootstrap 90% CI of expectancy above the baseline,
* beats the random-entry benchmark,
* a parameter plateau,
* at most 6 configurations.

## Configurations (6 max)

| id | N bars | limit price |
|----|--------|-------------|
| h1a | 4 | zone inner edge |
| h1b | 2 | zone inner edge |
| h1c | 8 | zone inner edge |
| h1d | 4 | zone centre |

## Measurements

* fill rate = filled entry signals / submitted entry signals,
* adverse selection: realised R of filled entries vs hypothetical R of
  cancelled entries at the same limit, stop and targets,
* fee and slippage cost per trade vs baseline,
* trade count and walk-forward fold signs.

## Risks

Adverse selection can make the saved fee irrelevant: the limits that fill are
exactly the ones where price keeps going through the zone. If the fill rate is
below roughly 30% or cancelled entries would have been materially better, the
hypothesis is rejected even if PF rises.
