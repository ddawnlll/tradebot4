# HYP-002: Stricter range width to cost filter

Status: proposed (round 1)

## Evidence

The frozen baseline accepts ranges whose full width is at least three times
the estimated round-trip cost (`min_width_cost_ratio=3.0`). With a 20 USDT
account and 2 bps/5 bps slippage, a range that is only 3x the cost leaves very
little room for error: the targets sit close to the entry, while the stop is
wide.

## Change (one change)

Raise `min_width_cost_ratio`. No other parameter changes.

## Preregistered criteria

Same as HYP-001. Fewer trades is an acceptable outcome only above the
100-trade floor; below it the hypothesis is rejected as underpowered.

## Configurations (6 max)

| id | min_width_cost_ratio |
|----|----------------------|
| h2a | 4.0 |
| h2b | 5.0 |
| h2c | 6.0 |
| h2d | 8.0 |

## Risks

The filter is correlated with volatility regime: it may simply select the
quietest months and produce a sample concentrated in a few folds. Per-fold
trade counts and fold signs are part of the decision.

## Result (2026-10-30)

Rejected. Ratio 4.0 produces 27 trades across four folds (PF 0.467,
expectancy -0.2975R); ratios 5.0, 6.0 and 8.0 produce zero trades. The frozen
baseline ratio 3.0 is already at the edge of tradability. The hypothesis is
underpowered and negative.
