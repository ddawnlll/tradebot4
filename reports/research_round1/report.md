# Research round 1: cost hypotheses on the frozen development split

Status: **NO_GO**. No cost hypothesis passes the frozen hypothesis gates.
The holdout (2026-07-01 .. 2026-10-20) was **not opened**.

## 1. What was tested

The round uses the development split frozen in `config/data_split.yaml`:
2024-11-01 .. 2026-06-30, 40 symbols, 1m execution with 15m/1h/4h
aggregation. The holdout is sealed. The gates in `config/gates.yaml` were
frozen before the first run. Each hypothesis is capped at six
configurations and evaluated on four walk-forward folds of five months
each, 20 symbols, starting from a fresh 20 USDT account per fold.

The explicit cost model (ADR 0007) charges market `max(2 bps, 1 tick)`
adverse, stop 5 bps adverse plus the bar gap, limit fills only after one
tick through the price with 90% touch probability, and maker/taker VIP0
fees. The cost stress multiplies commission by 1.5 and slippage by 2.0.

## 2. Baseline

### 2.1 Official validation, one run

| name | trades | gross | slippage | fees | funding | net | pf | exp | dd |
|---|---|---|---|---|---|---|---|---|---|
| old model / dev-A | 240 | 2.79 | 0.01 | 10.22 | -0.10 | -7.54 | 0.902 | -0.0262 | 50.2 |
| new model / dev-A | 103 | -3.53 | 3.30 | 4.25 | -0.08 | -7.85 | 0.767 | -0.0838 | 50.4 |
| old model / dev-B | 82 | -6.41 | 0.00 | 3.50 | 0.10 | -9.81 | 0.621 | -0.1506 | 50.5 |
| new model / dev-B | 55 | -7.56 | 2.42 | 2.28 | 0.02 | -9.82 | 0.502 | -0.2282 | 50.5 |

`dev-A` is 2024-11-01..2025-12-31; `dev-B` is the old out-of-sample
window 2026-01-01..2026-06-30, which is development data under the frozen
split. The old model had `prob_slippage=0` and a 100% touch fill; the new
model is the ADR 0007 model. The new dev-A run produced 103 trades and
hit the 50% peak-to-trough kill switch on 2025-01-19; the old model
reached its kill switch on 2025-05-20 after 240 trades. Metrics are
therefore `until ruin`, not full-window performance.

The cost decomposition of the new dev-A run: gross before slippage -0.22 USDT, slippage 3.30, fees 4.25, funding -0.08, net -7.85. The round trip costs 0.07-0.09 USDT, or 0.35-0.45% of starting equity per trade. The old gross +2.79 was a later, luckier slice of a path the realistic model did not survive; the first two months contain the same 89 entries and the new model is 2.9 USDT worse in gross there.

### 2.2 Four-fold baseline used for the hypothesis comparison

* trades 253, net -34.77 USDT, PF 0.625, expectancy -0.1697R
* 90% CI of mean R: [-0.2479, -0.0938]
* positive folds 0/4; fold net [-5.8983, -10.2295, -9.2246, -9.4161]
* random-entry benchmark: trades 198, net -29.70, PF 0.784, expectancy -0.1482R, positive folds 0/4

The baseline is **worse than random entries** on expectancy and PF. The
range filters are not adding value relative to a seeded random entry with
the same stops, targets, sizing and cost model. The dominant loss driver
is the breakout exit: in the single dev-A run, 42 breakout exits lost
30.68 USDT, while TP2 plus breakeven-stop wins earned 25.42 USDT.

## 3. Hypotheses

### H1: post-only limit at the zone edge

The entry is a post-only limit at the inner edge of the zone, kept for
N bars and cancelled if unfilled. Stops, targets, sizing and the time
stop are unchanged.

| config | trades | net | fold net | pf | exp | ci90 | folds + | fill | cancel | reject |
|---|---|---|---|---|---|---|---|---|---|---|
| h1a | 678 | -20.49 | -9.32, 4.31, -7.10, -8.39 | 0.919 | -0.0296 | [-0.0767, 0.0160] | 1/4 | 0.654 | 353 | 6 |
| h1b | 538 | -28.50 | -9.05, -4.38, -6.96, -8.11 | 0.850 | -0.0545 | [-0.1060, -0.0031] | 0/4 | 0.512 | 508 | 5 |
| h1c | 643 | -26.68 | -9.58, -0.10, -8.63, -8.37 | 0.870 | -0.0422 | [-0.0911, 0.0056] | 0/4 | 0.739 | 222 | 5 |
| h1d | 287 | -24.24 | -8.23, -6.86, -1.44, -7.70 | 0.825 | -0.0883 | [-0.1771, 0.0028] | 0/4 | 0.335 | 570 | 0 |

| group | n | fwd_close | ci_close | fwd_entry |
|---|---|---|---|---|
| filled | 678 | -0.26% | [-0.50%, -0.02%] | +0.14% |
| cancelled | 353 | +0.07% | [-0.22%, +0.38%] | +1.45% |

`fwd_entry` is measured from the limit price. For a cancelled entry this
is a counterfactual: the price never reached the limit, so the entry
would not have happened. The comparison is only a measure of what the
cancelled limit would have captured.

Harness note: the first H1 pass aborted 9 of 16 folds because the
`strict_errors` guard treats an expected post-only rejection as fatal.
Those failed trials are in the ledger. H1 was rerun with
`strict_errors=False`; the 16 completed folds are the result below.

H1a (N=4, edge) is the best H1 configuration by expectancy and PF: 678
trades, PF 0.919, expectancy -0.0296R, 90% CI [-0.0767, +0.0160], one
positive fold out of four. The fill rate is 65.4%; 353 limits were
cancelled and 6 were rejected as post-only because the market was
already at the limit. Under cost stress H1a has 606 trades, PF 0.860 and
expectancy -0.0507R.

Adverse selection is present but is not the main problem. Over the eight
hours after the signal, filled entries moved -0.26% from the signal close
(90% CI [-0.50%, -0.02%]); cancelled entries moved +0.07% (CI [-0.22%,
+0.38%]). The filled-minus-cancelled difference is -0.33% (90% CI
[-0.71%, +0.04%]), i.e. the limits that fill are the slightly worse ones,
at the edge of significance. The bigger issue is that even the entry
price improvement is too small to turn a negative gross edge positive.

### H2: stricter range width to cost ratio

| config | trades | net | pf | exp | ci90 | folds + |
|---|---|---|---|---|---|---|
| h2a | 27 | -7.75 | 0.467 | -0.2975 | [-0.5475, -0.0338] | 0/4 |
| h2b | 0 | 0.00 | - | - | [-, -] | 0/4 |
| h2c | 0 | 0.00 | - | - | [-, -] | 0/4 |
| h2d | 0 | 0.00 | - | - | [-, -] | 0/4 |

Raising `min_width_cost_ratio` from 3.0 to 4.0 collapses the sample to 27
trades across four folds; 5.0, 6.0 and 8.0 produce zero trades. The
baseline ratio 3.0 is already at the edge of tradability: the validated
ranges are narrow relative to the round-trip cost. H2 is rejected as
underpowered and negative.

### H3: 1h entry signals

| config | trades | net | pf | exp | ci90 | folds + |
|---|---|---|---|---|---|---|
| h3a | 88 | -6.85 | 0.794 | -0.0811 | [-0.2092, 0.0512] | 3/4 |
| h3b | 89 | -3.24 | 0.884 | -0.0391 | [-0.1543, 0.0787] | 2/4 |

The 1h entry cadence produces 88 trades (32-bar time stop) and 89 trades
(8-bar time stop) across four folds, below the 100-trade floor. The 8-bar
time stop is the least negative H3 configuration (PF 0.884, expectancy
-0.0391R, CI [-0.154, +0.079], two positive folds), second only to H1a
on the point estimate, and under
stress it is PF 0.795 / -0.0702R. It beats the random benchmark but fails
the PF and trade-count gates; it is not a candidate.

## 4. Gate results

| hypothesis | best config | trades | PF | exp R | CI90 low | stress PF | plateau | verdict |
|---|---|---|---|---|---|---|---|---|
| H1 | h1a | 678 | 0.919 | -0.0296 | -0.0767 | 0.860 | no | **FAIL** |
| H2 | h2a | 27 | 0.467 | -0.2975 | -0.5475 | 0.418 | no | **FAIL** |
| H3 | h3b | 89 | 0.884 | -0.0391 | -0.1543 | 0.795 | no | **FAIL** |

The frozen criteria are: at least 100 walk-forward trades, PF >= 1.10,
PF >= 1.00 under cost stress, 90% bootstrap CI lower bound above the
baseline mean R, beats the random benchmark, and a parameter plateau.
No configuration passes all six. H1a passes the trade count, the CI and
the random test but fails PF, stress PF and plateau. H3b passes the CI
and the random test but fails the trade count, PF, stress PF and plateau.
H2 fails the trade count and PF.

## 5. Holdout

The holdout was not opened. No hypothesis produced a candidate that
passed the frozen gates, so no candidate justification was committed and
the one permitted holdout opening was preserved. The validation vault
was skipped because the development OOS segment failed its core criteria;
no run in this round touched 2026-07-01..2026-10-20. The holdout manifest
hash remains `4768406fa559fc4fc0f2124fc946a262`.

## 6. What this means

1. The realistic cost model is not the only problem. The gross PnL before
   costs is negative or near zero, the baseline is worse than random, and
   the breakout exit is the largest loss driver. A cheaper entry cannot
   fix a negative gross edge.
2. The strategy ruins. The baseline has zero positive folds and the
   single dev-A run hits the 50% kill switch in three months. H1a has
   one positive fold and H3b two, but both still lose after costs. The
   four-fold baseline is a sequence of four fresh 20 USDT accounts, not
   one account; each fold is truncated by ruin when it hits the limit.
3. The range width/cost filter has no room. The current 3.0 ratio is the
   maximum that still produces a sample; 4.0 leaves 27 trades and 5.0
   leaves none.
4. 1h signals reduce frequency without creating an edge. The 8-bar time
   stop is less bad than 32 bars, but 89 trades and PF 0.884 are not
   enough.

## 7. Suggested next directions (maximum 3, same family)

1. **Exit and tail risk.** The 42 breakout exits in dev-A lost 30.68 USDT
   against 25.42 USDT of TP2 plus breakeven wins. Test a preregistered
   exit redesign: invalidate earlier, or replace the breakout close exit
   with the hard stop, with a maximum of six configurations. This is the
   largest single loss driver and the only change with a plausible path
   to a positive gross edge.
2. **Range persistence and quality.** The current score rewards width,
   touches and duration but not whether the range has repeatedly mean-
   reverted. Test a small set of age, touch-quality and volatility-
   compression filters on the development folds.
3. **Stop at 20 USDT.** A round trip costs 0.35-0.45% of starting equity,
   and the observed gross edge is negative. If directions 1 and 2 fail,
   the honest conclusion is that this family is not viable at this
   account size; raising the account is a capital decision, not a
   strategy fix.

## 8. Artifacts and reproduction

* `reports/baseline_costed/baseline.md` and `.json`: the costed baseline,
  cost decomposition, stress and resources.
* `reports/research_round1/summary.json`: every fold and aggregate.
* `reports/research_round1/gates.json`: the recomputed frozen gates.
* `reports/research_round1/h1_adverse_selection.json`: fill/adverse
  selection measurement.
* `kaizen/experiments.parquet`: append-only ledger, including the failed
  H1 first pass (see the ledger note).
* `kaizen/hypotheses/HYP-001..003.md`: preregistered hypotheses.

```bash
uv run bot validate --symbols universe --start 2024-11-01 \
  --dev-end 2025-12-31 --oos-end 2026-06-30 --vault-end 2026-10-20 --workers 4
uv run python scripts/run_stress.py
uv run python scripts/run_hypothesis.py --hypothesis all --workers 4
uv run python scripts/evaluate_hypotheses.py
uv run python scripts/h1_adverse_selection.py
```
