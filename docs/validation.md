> **Phase 2 update (2026-10-30).** The explicit cost model and the
> research round 1 are complete. The costed baseline and all three cost
> hypotheses are **NO_GO**; the holdout 2026-07-01..2026-10-20 was not opened.
> See `reports/research_round1/report.md` and
> `reports/baseline_costed/baseline.md`. This document remains the Phase 1
> record; its vault was not opened.

# Milestone 6 validation report - GO / NO_GO

**Verdict: NO_GO.**

Date: 2026-10-29. Pinned engine: `nautilus_trader==2.0.0rc8`, Python 3.13.9.
Report artifact: `reports/validation_20261029T214439Z/validation.json`.
No live execution code was written. The vault period was not opened.

## Summary

| Test | Result |
|---|---|
| Champion, development (40 symbols, 2024-11-01 to 2025-12-31) | 240 trades, net -7.54 USDT (-37.8%), PF 0.90, expectancy -0.026R, max DD 50.3% |
| Walk-forward (4 consecutive windows inside development) | 1 of 4 windows with positive expectancy; 3 negative |
| Parameter sensitivity (11 variants, 12 symbols) | 11 of 11 negative expectancy |
| Ablations (regime / higher-TF / breakout exit / single TP) | 4 of 4 negative |
| Out of sample (40 symbols, 2026-01-01 to 2026-06-30, opened once) | 82 trades, net -9.81 USDT (-49.0%), PF 0.62, expectancy -0.151R, max DD 50.5% |
| OOS 95% bootstrap CI of mean R | iid [-0.309, +0.013]; block [-0.300, +0.005]; P(mean > 0) = 3.5% |
| Probabilistic / deflated Sharpe (OOS) | PSR 0.042; DSR approximately 0 |
| Monte Carlo, 10,000 paths, block bootstrap | P(reach 40 USDT) 5.9%; P(ruin at 10 USDT) 47.2%; median final 12.69 USDT; median max DD 56.9% |
| GO criteria | **0 of 9 passed**; all nine failed |

The first generated artifact marked the drawdown criterion as passed because
`max_drawdown_pct` was stored as a fraction (0.505) while the threshold was
30.0. The unit bug was found, fixed, and the report regenerated; the criterion
now correctly fails. Every other criterion failed independently.

## Tooling verification (observed, not inspected)

| Check | Observation |
|---|---|
| Test suite | 56 tests pass (38 core/integration + 18 dashboard), `ruff` clean |
| Deterministic replay | Two identical WIFUSDT backtests produced byte-identical trades, fills, orders and signals, and identical metrics |
| Dashboard | All 7 pages rendered the 240-trade champion through Streamlit AppTest with 0 exceptions; HTTP smoke test returned 200 |
| CLI | `status`, `scan`, `backtest`, `data catalog`, `data sync`, `journal enrich` executed; `risk pause` and `rollback` exited 2 with the live gate message |
| Journal | DuckDB views returned 2,539,399 events, 322 trades, 212,255 signals |
| Reconciliation | Development residual -0.020 USDT, OOS +0.008 USDT |
| OOS robustness | 82 trades, zero bridge errors |

Before the protective-order fixes the OOS run aborted after 3 reduce-only
rejections. After the fixes it completed. This confirms the measurement is
sound; it does not turn the negative expectancy into an edge.

## The economic finding

The champion is close to breakeven *before fees* in development:

| Period | Gross PnL | Fees | Funding | Net |
|---|---:|---:|---:|---:|
| Development (until the kill switch) | +2.79 | -10.22 | -0.10 | -7.54 |
| Out of sample | -6.41 | -3.50 | +0.10 | -9.81 |

At 20 USDT, 240 trades cost 10.22 USDT in fees, about 51% of the starting
capital. A strategy with a ~0.01R gross edge per trade cannot survive a ~0.04R
fee per trade. In OOS the gross edge is negative as well, so fees are not the
only problem: the entry signal has no stable edge.

The exit structure is not the culprit. Across the development run:

| Exit | Trades | Net PnL | Expectancy |
|---|---:|---:|---:|
| TP2 | 67 | +50.73 | +0.82R |
| Breakeven stop after TP1 | 63 | +16.86 | +0.28R |
| TP1 (single-leg fallback) | 4 | +1.43 | +0.46R |
| Time stop | 3 | -0.69 | -0.21R |
| Initial stop | 7 | -6.52 | -0.98R |
| Breakout exit | 95 | -69.72 | -0.77R |

Winners are frequent but small; 95 range failures at -0.77R dominate. Removing
the breakout exit does not fix it (ablation: -0.137R vs -0.132R), because the
losses move to the native stop. The problem is the entry, not the exit.

## Benchmarks (out-of-sample period, same costs and risk)

| Strategy | Trades | Net PnL | Expectancy | PF |
|---|---:|---:|---:|---:|
| Range V1 (champion) | 82 | -9.81 | -0.151R | 0.62 |
| Trend pullback | 40 | -4.60 | -0.083R | 0.85 |
| Seeded random entry | 36 | -10.06 | -0.373R | 0.48 |

Range V1 beats random entries on OOS, so the signal is not pure noise, but it
is worse than a simple trend-pullback benchmark and all three lose. The
automated GO check compares OOS expectancy with a *development-period* random
benchmark (+0.056R), which makes the random benchmark look better than it was
on OOS. Even if that criterion is credited after recomputing it on the OOS
period, eight of nine criteria still fail.

## What was fixed during validation

Engineering defects found and fixed before the final numbers:

1. Zone reads ignored `min_touches`, so single-touch pivots could define a
   range. Fixed and regression-tested.
2. Entries could close anywhere above the support mid, not inside the zone as
   specified. This destroyed the TP1-to-stop geometry. Fixed and tested.
3. A breakeven stop after TP1 could be placed on the wrong side of the market
   when price gapped. The move is now skipped and deferred to the next bar.
4. A market order can fill in slices. The bridge now keeps the client-order
   mapping until the order is complete and resizes protection accordingly.
5. `on_entry_rejected` could drop an *open* position, leaving the broker
   position unmanaged and losing its trade record. Fixed.
6. Partial exits could race new protective orders, producing reduce-only
   rejections. Exits now take precedence over protection, and protection is
   deferred while an exit is in flight.
7. Quantities below one step are force-closed in the core and recorded, so the
   account-vs-trades residual stays near zero (0.02 USDT in development,
   0.008 USDT in OOS).

These fixes changed the results materially; earlier numbers from buggy builds
are archived under `runs/_pre_final` and are not used in this report.

## Monte Carlo

10,000 paths, block bootstrap of the champion's development R sequence, 5%
risk per trade, starting equity 20 USDT:

| Metric | Value |
|---|---:|
| P(equity reaches 40 USDT) | 5.9% |
| P(equity falls to 10 USDT) | 47.2% |
| P(end above start) | 20.6% |
| Median final equity | 12.69 USDT |
| 5th percentile final equity | 5.09 USDT |
| Median max drawdown | 56.9% |
| 95th percentile max drawdown | 79.3% |

A strategy with a 47% chance of losing half the account before a 6% chance of
doubling it is not investable at this size.

## Assumptions that could still invalidate the result

* Bar execution cannot resolve intrabar stop-vs-target ordering. The ambiguity
  audit found 0 trades in the development run where both levels were inside the
  same 1m bar, so this is not driving the result.
* Limit take-profits assume a fill on touch (`prob_fill_on_limit = 1.0`);
  queue position would make results worse, not better.
* Stops trigger on `LAST` in the backtest; live must use `MARK_PRICE` and be
  validated on testnet.
* Funding uses the 1m close at settlement instead of the mark price.
* No latency, market impact or exchange outages are modelled.
* The universe is frozen at 2024-11-01 (top 40 by trailing 24h quote volume)
  and does not re-rank; delisted symbols drop out when their data ends.

## What was not done

* Milestone 7 (live execution), 8 (kaizen automation) and 9 (live readiness)
  were not started. The task gates them on a GO.
* The vault window (2026-07-01 to 2026-10-20) remains sealed. It was not
  opened because OOS failed the core criteria, and it should not be used to
  rescue a failed hypothesis.

## Next hypotheses (not evaluated on OOS)

Each must be pre-registered, tested on development, and only then taken to a
fresh out-of-sample window. None may be tuned against the current OOS.

1. **Entry confirmation is wrong.** The rejection bar enters while the level is
   still being tested. A hypothesis: require the *next* 15m bar to hold the
   zone before entering, or invert the setup to a breakout-retest.
2. **Fee drag is structural.** At 240 trades per 6.5 months, fees are 51% of
   capital. Fewer, larger-edge trades or maker entries (post-only at the zone
   inner edge, V2) could change the economics. V2 is implemented as a variant
   but was not validated.
3. **Range quality filter is not selective enough.** Two-touch zones may be
   noise. A stricter definition (3+ touches, minimum duration, containment)
   should be tested as a single change with an ablation.
4. **Directional conditioning.** Range shorts and longs performed differently
   in different periods; a trend-regime filter may need to be stricter, not
   looser. The current ablation of the 1h filter increases trades and still
   loses.
5. **The exit ladder may be too slow.** TP1 at the midpoint and a breakeven
   stop produce many small wins; a faster partial at 0.5R or a trailing stop
   could change the payoff, at the cost of more stop-outs.

## Reproduction

```bash
uv run bot validate --symbols universe --start 2024-11-01 \
  --dev-end 2025-12-31 --oos-end 2026-06-30 --vault-end 2026-10-20 --workers 4
```

Report: `reports/validation_<timestamp>/validation.md`. The journal, trade
records and fills for every run are under `runs/<run_id>/`.
