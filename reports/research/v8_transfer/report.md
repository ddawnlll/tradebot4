# V8-core transfer: exit, MarketState, no-trade, capital and expert assessment

**Verdict: NO_GO.** A partial loss reduction was found, but no candidate reaches
the frozen profitability gates, the baseline remains worse than random entries
with the same exit, and the sealed holdout 2026-07-01..2026-10-20 was not opened.

Date: 2026-10-30. Target: tradebot4 `main`, starting `6e7483c`; final commit SHA
is reported with the delivery. Source: `~/src/v8` at `e49e09f` (start) /
`ee60eac` (during), `v8-core` last commit `0990615`; relevant source files were
clean and hashed in `docs/research/v8_transfer_inventory.md`. V8 was read-only.

## 1. What was transferred

| Area | Result | Evidence |
|---|---|---|
| Exit policy | 15-arm conditional 1m diagnostic + full Nautilus replay of 2 finalists | `exit_scan.md/json`, `exit_replay.md/json` |
| MarketState features | 36 observational, versioned, PIT features; descriptive separation | `src/bot/core/features.py`, `feature_ablation.md/json` |
| Chop/no-trade | A2 failure cooldown, A3 compression, A5 A2+A3; A1 data-blocked | `chop_scan.md/json` |
| Capital viability | E_step/E_notional/E_margin/E_crit + fee reserve, 20 and 500 as separate tiers | `capital_viability.json`, `src/bot/risk/capital.py` |
| Expert assessment | 5 experts assessed; `failed_breakout_2b` selected as next, not implemented | `docs/research/v8_expert_assessment.md` |
| Inventory | every important v8-core idea classified with file:line and data blockers | `docs/research/v8_transfer_inventory.md` |

The default strategy decision path was **not** changed by features, chop or
capital work. The only strategy-path change is the research-only
`static_target_r` exit configuration, tested in the full replay.

## 2. Baseline identity

| Field | Value |
|---|---|
| Validation | `reports/validation_20261030T115653Z/validation.json` |
| Run | `20261030T115653Z_range_v1_dacaa5eb69700faa` |
| Config hash | `dacaa5eb69700faa` |
| Engine | NautilusTrader 2.0.0rc8, Python 3.13.9, seed 42 |
| Development | 2024-11-01..2026-06-30; dev-A 2024-11..2025-12, dev-B 2026-01..06 |
| Account | 20 USDT, leverage 10, risk 5%, two-leg TP, kill switch 50% |
| 1m data hash | `7df0d426...` (959 files, sorted content digest) |
| Funding hash | `92f86a06...` |
| Baseline dev-A | 103 trades, net -7.8536, PF 0.7674, -0.0838R, DD 50.45% |
| 4-fold baseline | 253 trades, net -34.77, PF 0.625, -0.1697R, 0/4 folds |

The 4-fold baseline is four fresh 20 USDT accounts, not one compounded account.
The sealed holdout was not read by any script in this task.

## 3. Exit diagnostic (conditional, not portfolio evidence)

The 1m scan replayed alternative exits on the same 103 entries/quantities. It
does not re-simulate future entries, overlap, equity or the kill switch.
Selected results (all 15 arms in `exit_scan.md`):

| arm | n | mean R | PF | max DD R | note |
|---|---:|---:|---:|---:|---|
| baseline actual | 103 | -0.0838 | 0.763 | 13.13 | TP1/TP2/BE/breakout |
| no breakout | 103 | -0.0571 | 0.862 | 14.97 | TP1/TP2, stop |
| stop only | 103 | +0.0277 | 1.050 | 16.23 | no TP, 8h time stop |
| static 1R | 103 | -0.0109 | 0.978 | 14.15 | single target |
| static 2R | 103 | +0.1451 | 1.241 | 14.39 | single target |
| static 3R | 103 | +0.1575 | 1.225 | 14.68 | single target |
| breakout at zone (0.0 ATR) | 103 | -0.0351 | 0.885 | 10.19 | earlier invalidation |
| chandelier 2.5 ATR | 103 | -0.1913 | 0.655 | 25.69 | trailing |
| BE 1.0R | 103 | -0.1656 | 0.676 | 23.03 | premature BE |
| structural 24h | 103 | +1.357 | 3.113 | 16.99 | **not advanced**: 7-day horizon, one DOGE +46.9R outlier |
| structural 48h | 101 | +1.666 | 3.273 | 20.96 | **not advanced**: path/overlap changes |

The structural arms looked spectacular because they held positions for days;
a single DOGE trade contributed +46.9R and the arm changes the entire entry
overlap and capital path. They are diagnostic only, not a finalist.

**Finalists:** `static_2r` and `static_3r` (bounded, path-compatible). The
selection came from the same conditional scan; this is a known selection-bias
risk.

## 4. Full Nautilus replay

4 development folds, 20 symbols, fresh account per fold, frozen cost model.
Static target replaces TP1/TP2/BE/breakout with one target; entry/stop/risk
unchanged.

### 20 USDT

| arm | trades | net | PF | exp R | 90% CI | folds + | stress PF |
|---|---:|---:|---:|---:|---|---:|---:|
| baseline | 253 | -34.77 | 0.625 | -0.1697 | [-0.2479, -0.0938] | 0/4 | - |
| static_2r | 133 | -34.36 | 0.625 | -0.3017 | [-0.4791, -0.1133] | 0/4 | 0.579 |
| static_3r | 108 | -35.53 | 0.537 | -0.3877 | [-0.6184, -0.1402] | 0/4 | 0.466 |

**Gate result: both fail.** Trades >=100 pass (barely for 3R). PF >=1.10 fails,
stress PF >=1.00 fails, the CI is below zero and below the baseline mean,
`beats_random_same_exit` fails, and the plateau fails. The static arms also
produce fewer trades because a position without a breakout/time exit stays open
longer and blocks later entries. Pure static is not an improvement.

For transparency, an earlier replay pass left the baseline breakout and 8h
time stop enabled, so it measured a hybrid (static target + breakout/time):
`static_2r` 210 trades, -29.21, PF 0.734, -0.1503R, stress 0.676. That hybrid
improved the baseline's loss but still failed every gate and was worse than
random_2R. It is not the isolated static arm and is retained only as a
diagnostic.

### Random entry with the same exit

| arm | trades | net | PF | exp R | folds + |
|---|---:|---:|---:|---:|---:|
| random actual | 198 | -29.70 | 0.784 | -0.1482 | 0/4 |
| random 2R | 296 | **-6.04** | **0.979** | **-0.0020** | 1/4 |
| random 3R | 125 | -27.21 | 0.733 | -0.2244 | 0/4 |

This is the central result: **random entries with the 2R exit are far better
than the range strategy with the same exit.** The range entry, not the exit
alone, is destroying value. Static_2r therefore fails `beats_random_same_exit`.
The near-breakeven random_2R is a benchmark, not a tradable strategy and not a
GO.

### 500 USDT (separate, not scaled)

| arm | trades | net | PF | exp R |
|---|---:|---:|---:|---:|
| baseline | 265 | -840.01 | 0.648 | -0.1527 |
| static_2r | 155 | -851.07 | 0.672 | -0.2505 |
| static_3r | 110 | -887.50 | 0.554 | -0.3764 |

Both accounts lose. The 500 USDT result is not a scaled 20 USDT result and is
not positive; undercapitalization is not the only cause.

### Why the offline scan was optimistic

`static_2r` was +0.1451R in the conditional scan and **-0.3017R** in the pure
full replay, a **0.447R** gap. The hybrid pass was -0.1503R. The scan held
entries and quantities fixed, so it could not see that different exits change
the next entries, overlap, capital and kill-switch timing. The scan is a
**diagnostic**, never a portfolio claim.

The pure static arm also shows the task's warning directly: removing the
breakout/time stop did not reduce the loss; it made the per-trade result worse
and reduced the trade count from 253 to 133.

## 5. MarketState features

36 observational features were implemented with version, availability, quality
and null reasons: candle shape, BB/ATR expansion, prior/window extremes,
confirmed swings, volume z-score, CMF20, UTC session. No feature is wired into
the strategy. Descriptive AUC on the 103 baseline entries:

| feature | coverage | AUC win | AUC breakout |
|---|---:|---:|---:|
| `body_range_ratio` | 1.00 | 0.651 | 0.319 |
| `cmf_20` | 1.00 | 0.644 | 0.364 |
| `inside_bar` | 1.00 | 0.432 | 0.578 |
| `vol_zscore_100` | 1.00 | 0.526 | 0.464 |

These are univariate, multiple-tested and conditional; not an edge. They are
candidates for one preregistered filter, not a feature dump. See
`feature_ablation.md`.

## 6. Chop / no-trade

- A1 (expected excursion vs friction): **DATA_BLOCKED**. The only available
  excursion is future MFE, which cannot be used at entry.
- A2 (8-bar failure cooldown): 99 kept, mean R -0.1086, PF 0.699. **Worse**
  than the baseline; rejected.
- A3 (ATR14 / median ATR14(50) >= 0.65): no trades vetoed at 0.65; no effect.
- A5 (A2+A3): equals A2; negative. Not the full v8 composite because A1 is
  blocked.

## 7. Capital viability

The v8 E_crit envelope was evaluated with independent 20 and 500 tiers and
risk-sized quantities per tier:

| tier | candidates | approved | rejected | binding constraints |
|---|---:|---:|---:|---|
| 20 USDT | 158 | 155 | 3 | notional 143, step 15 |
| 500 USDT | 158 | 158 | 0 | notional 143, step 15 |

At 20 USDT, 3 historical envelopes fail min notional/quantity/step; at 500 all
are representable. This is a **static diagnostic**, not a guarantee of fills or
profit. The full replay shows 500 USDT still loses, so removing the minimum
constraint does not create an edge. 77 baseline rows had a final/breakeven stop
on the wrong side of entry; the initial stop distance was reconstructed from
recorded risk/qty and labeled.

## 8. Expert assessment

`failed_breakout_2b` is selected as the single next expert candidate: it
directly addresses the 42 breakout exits, uses only OHLCV/ATR/swing/candle
features, and has a frozen structural stop. `liquidity_sweep_reclaim` is the
second candidate. `ichimoku_cloud` and `trend_continuation` have small,
burned-window v8 samples and require a trend regime; they are not validation.
**Not implemented in this task** because the exit replay had to be interpreted
first; status `NEXT_STEP`, no data blocker. See
`docs/research/v8_expert_assessment.md`.

## 9. What changed in the code

- `src/bot/research/exit_scan.py`: conditional 15-arm exit diagnostic.
- `src/bot/research/chop_scan.py`: A2/A3/A5 diagnostic; A1 blocked.
- `src/bot/core/features.py`: 36 observational PIT features.
- `src/bot/risk/capital.py`: static capital envelope, 20/500 tiers.
- `src/bot/core/engine.py` + `src/bot/backtest/runner.py`: research-only
  `static_target_r` exit configuration; default unchanged.
- `src/bot/audit/why.py` and `validate.py`: run/config/period identity,
  visible audit failure, atomic/idempotent history.
- `scripts/`: exit scan/replay, chop, feature ablation, capital diagnostics.
- Tests: 154 pass; ruff clean. The audit history was upgraded without data loss.

## 10. Decision and next step

**NO_GO.** Pure static 2R has essentially the same net as the baseline but a
worse PF and mean R, fails every robustness gate, and is far worse than random
entries with the same exit. The hybrid static+breakout variant also loses. The
sealed holdout was not opened. A development pass would only be INCONCLUSIVE.

**Single next research step:** test `failed_breakout_2b` as a separate strategy
against the **random-2R benchmark**, not against the old range baseline. The
random-2R near-breakeven result says the range entry is the weak link; a new
entry mechanism must beat that benchmark after costs, with 100+ trades, stress
PF >=1.00 and a plateau. Do not add more exit arms or features before that
test.

## Reproduce

```bash
uv run python scripts/exit_scan.py
uv run python scripts/exit_replay.py
uv run python scripts/feature_ablation.py
uv run python scripts/chop_scan.py
uv run python scripts/capital_viability.py
uv run pytest tests -q
```
