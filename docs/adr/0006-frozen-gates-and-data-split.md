# ADR 0006: Frozen gates and data split for research round 1

Status: accepted (2026-10-30)

## Context

The reference specification file `~/src/tradebot_prompt.md` was not present in
the environment, so section 8c thresholds could not be copied verbatim. The
previous Milestone 6 report already had an explicit gate set, and the task adds
hypothesis-level criteria. The old OOS window (2026-01-01..2026-06-30) has been
seen and can no longer be used as out-of-sample data. The catalog ends at the
1m bar closing 2026-10-21T00:00:00Z, which leaves 3.7 months of never-opened
data.

## Decision

1. `config/gates.yaml` is frozen. Holdout gates: at least 100 trades, PF > 1.20,
   positive expectancy, max drawdown <= 30%, block-bootstrap 95% lower bound
   above zero, beats a random-entry benchmark, walk-forward majority positive,
   and a parameter plateau.
2. Hypothesis gates: at least 100 walk-forward trades, PF >= 1.10, PF >= 1.00
   under the 1.5x commission / 2x slippage stress, a 90% bootstrap improvement
   over baseline, beats random, and a parameter plateau.
3. `config/data_split.yaml` is frozen. Development is 2024-11-01..2026-06-30.
   Holdout is 2026-07-01..2026-10-20 (last bar closes 2026-10-21T00:00:00Z).
4. The holdout manifest hash is the SHA-256 of the names, sizes and contents of
   the 160 canonical parquet files for the 40-symbol universe in the holdout
   months. It is `4768406fa559fc4fc0f2124fc946a262`.
5. The holdout is opened at most once in this round. Every opening is recorded
   in `kaizen/experiments.parquet` with the candidate, timestamp and reason.

## Consequences

Any later change to the split or gates invalidates this round. The old OOS
metrics remain in the historical Milestone 6 report but are not used as
out-of-sample evidence again.
