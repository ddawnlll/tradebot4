# HYP-006: Observational MarketState batch 1

Status: observational, not a trading hypothesis.

## Scope

A versioned feature snapshot fed only by closed bars: candle shape, BB
mid/upper/lower/pct_b/bandwidth, ATR14/2-sigma/trend phase, prior/window
extremes, confirmed swing 10/20, volume z-score, CMF20, UTC session. The
default strategy is not changed.

## Criteria

No decision uses these features until a separate preregistered hypothesis names
one feature, direction, threshold and costed walk-forward test. The first
diagnostic is univariate only and carries a multiple-testing warning.

## Result

See `reports/research/v8_transfer/feature_ablation.md`. `body_range_ratio`
(AUC 0.651 win, 0.319 breakout) and `cmf_20` (0.644, 0.364) separate the 103
baseline entries descriptively; this is not an edge and has not been tested
out of sample. No feature was wired into the strategy.
