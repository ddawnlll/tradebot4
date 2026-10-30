# ADR 0008: Holdout opening policy for research round 1

Status: accepted (2026-10-30)

## Context

The frozen split reserves 2026-07-01..2026-10-20 as a holdout. The round
allows at most one opening, and only for a candidate that is justified before
the opening. A holdout result cannot be used to tune the candidate.

## Decision

1. A candidate is eligible only if it passes every hypothesis criterion in
   `config/gates.yaml` on the development walk-forward: at least 100 trades,
   PF >= 1.10, PF >= 1.00 under cost stress, the 90% bootstrap lower bound
   above the baseline mean R, beats the random benchmark, and a parameter
   plateau.
2. Before opening, a candidate justification must be committed: the exact
   strategy, parameters, data slice, development metrics, gate results and the
   command to run.
3. The holdout is run once, on all 40 symbols, with the frozen cost model and
   without changing any parameter.
4. If no candidate is eligible, the holdout stays sealed. No exploratory run
   on 2026-07-01..2026-10-20 is permitted.

## Result for round 1

No candidate passed. H1a (678 trades, PF 0.919), H3b (89 trades, PF 0.884)
and H2a (27 trades, PF 0.467) all fail at least the PF gate; H2 and H3 also
fail the trade-count gate. The holdout was not opened and the manifest hash
remains `4768406fa559fc4fc0f2124fc946a262`.
