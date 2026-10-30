# HYP-004: Exit policy — static R target vs TP1/TP2/BE/breakout

Status: registered before the full Nautilus replay. The preceding 1m offline
scan was **exploratory** and was used only to select finalists; its conditional
trade set is not a portfolio result.

## Evidence

The costed baseline audit: 42 BREAKOUT exits, -30.68 USDT, 90.8% of negative
PnL; TP2 + breakeven + TP1 +25.92 USDT. The baseline TP1 is close and the
breakout exit realizes the tail. V8-core `exit_trailing.rs` and
`exit_ablation.rs` define static/trailing arms.

## Change (one family)

Replace the two-leg TP1/TP2/BE/breakout exit with one static R target while
keeping the entry, stop distance, risk fraction, sizing and time stop
unchanged. Two finalists selected from the conditional scan:

* `static_2r`: single target at entry +/- 2R.
* `static_3r`: single target at entry +/- 3R.

The offline scan also showed structural 24h/48h tails with large mean R, but
that arm held positions up to seven days and was dominated by a few outliers;
it was **not** advanced because it changes entry overlap and capital path. It
is recorded as diagnostic only.

## Criteria

4 development folds, 20 symbols, fresh 20 USDT account per fold, same frozen
cost model, commission x1.5/slippage x2 stress, random-entry benchmark with the
same static exit, 100+ trades, PF >= 1.10, stress PF >= 1.00, 90% CI lower
bound above the baseline mean R, and both static arms positive (plateau).
The sealed holdout 2026-07-01..2026-10-20 is not opened. A development pass is
at most `INCONCLUSIVE / GO-candidate`.

## Configurations (2)

| id | static target |
|---|---|
| static_2r | 2R |
| static_3r | 3R |

## Result (2026-10-30)

Full Nautilus replay, 4 development folds, 20 symbols, 20 USDT per fold:

| arm | trades | net | PF | exp R | 90% CI | folds + | stress PF |
|---|---:|---:|---:|---:|---|---:|---:|
| baseline | 253 | -34.77 | 0.625 | -0.1697 | [-0.2479, -0.0938] | 0/4 | - |
| static_2r | 133 | -34.36 | 0.625 | -0.3017 | [-0.4791, -0.1133] | 0/4 | 0.579 |
| static_3r | 108 | -35.53 | 0.537 | -0.3877 | [-0.6184, -0.1402] | 0/4 | 0.466 |

Pure static does not improve PF or mean R, all four folds are negative, stress
PF is 0.579/0.466, and the same-exit random benchmark is far better
(`random_2r`: 296 trades, net -6.04, PF 0.979, -0.0020R, CI [-0.135, +0.135]).
**Rejected.** The offline scan's +0.1451R for static 2R did not survive the
pure full replay (-0.3017R), a 0.447R gap. The hybrid pass that left breakout
and the 8h time stop enabled was -0.1503R; still rejected.

The 500 USDT scenario is separate and not scaled: baseline -840.01 (PF 0.648),
static_2r -851.07 (PF 0.672), static_3r -887.50 (PF 0.554). It remains negative;
the loss is not an undercapitalization artifact.
