# Costed baseline

Generated: 2026-10-30T11:56:53.814563+00:00

Two cost models are shown side by side. The old model is the Milestone 6
validation (`prob_slippage=0`, `prob_fill_on_limit=1.0`). The new model is
ADR 0007: market `max(2 bps, 1 tick)` adverse, stop 5 bps adverse plus the
bar gap, limit fills only after one tick through the price with 90% touch
probability, maker/taker VIP0 fees, funding reconstructed from history.

`dev-A` is 2024-11-01..2025-12-31. `dev-B` is 2026-01-01..2026-06-30,
the old out-of-sample window; under the frozen split it is development
data. The true holdout 2026-07-01..2026-10-20 was not opened (the
validation skipped the vault because dev-B failed its core criteria).

## Main comparison (20 USDT, 5% risk)

| name | trades | gross_pnl | slippage | fees | funding | net_pnl | return_pct | profit_factor | expectancy_r | win_rate | max_drawdown_pct |
|---|---|---|---|---|---|---|---|---|---|---|---|
| old model / dev-A | 240 | 2.79 | 0.01 | 10.22 | -0.10 | -7.54 | -37.79 | 0.90 | -0.03 | 0.56 | 50.25 |
| new model / dev-A | 103 | -3.53 | 3.30 | 4.25 | -0.08 | -7.85 | -39.35 | 0.77 | -0.08 | 0.56 | 50.45 |
| old model / dev-B | 82 | -6.41 | 0.00 | 3.50 | 0.10 | -9.81 | -49.02 | 0.62 | -0.15 | 0.46 | 50.51 |
| new model / dev-B | 55 | -7.56 | 2.42 | 2.28 | 0.02 | -9.82 | -49.08 | 0.50 | -0.23 | 0.44 | 50.52 |

Old gross is reconstructed as `net + fees - funding` because the old
fills did not record a reference price; its measured slippage was ~0.

## New model cost decomposition

| slice | gross_before_slippage | slippage_usdt | fees | funding | net_pnl | net_before_cost | residual |
|---|---|---|---|---|---|---|---|
| dev | -0.22 | 3.30 | 4.25 | -0.08 | -7.85 | -4.55 | -0.00 |
| OOS | -5.13 | 2.42 | 2.28 | 0.02 | -9.82 | -7.40 | 0.00 |

### Adverse price by fill kind

| slice | kind | fills | notional | adverse_usdt | avg_bps |
|---|---|---|---|---|---|
| dev | ENTRY | 103 | 4,821.50 | 1.04 | 2.17 |
| dev | TP1 | 58 | 1,337.42 | 0.00 | 0.00 |
| dev | TP2 | 23 | 548.96 | -0.00 | -0.00 |
| dev | STOP | 36 | 919.41 | 1.82 | 19.85 |
| dev | EXIT | 43 | 2,009.20 | 0.43 | 2.15 |
| OOS | ENTRY | 55 | 2,569.05 | 0.95 | 3.68 |
| OOS | TP1 | 24 | 545.74 | 0.00 | 0.00 |
| OOS | STOP | 14 | 513.94 | 1.02 | 19.92 |
| OOS | EXIT | 24 | 1,110.64 | 0.45 | 4.07 |
| OOS | TP2 | 17 | 394.78 | 0.00 | 0.00 |

`avg_bps` is adverse price movement versus the intended price, not the fee.

## Cost per trade

| run | fees_per_trade | slippage_per_trade | net_per_trade | cost_pct_of_equity_per_trade |
|---|---|---|---|---|
| new dev-A | 0.04 | 0.03 | -0.08 | 0.37 |
| new dev-B | 0.04 | 0.04 | -0.18 | 0.43 |

The account is 20 USDT; a round trip costs roughly 0.07-0.09 USDT,
or 0.35-0.45% of starting equity per trade.

## Why the old gross was positive

| run | month | trades | gross | fees | net |
|---|---|---|---|---|---|
| old dev-A | 2024-11 | 46 | 5.31 | 1.70 | 3.58 |
| old dev-A | 2024-12 | 43 | -3.33 | 2.17 | -5.56 |
| old dev-A | 2025-01 | 28 | -0.04 | 1.17 | -1.20 |
| old dev-A | 2025-02 | 38 | 4.40 | 1.51 | 2.89 |
| old dev-A | 2025-03 | 38 | -1.18 | 1.94 | -3.10 |
| old dev-A | 2025-04 | 27 | 1.11 | 1.05 | 0.04 |
| old dev-A | 2025-05 | 20 | -3.49 | 0.69 | -4.19 |
| new dev-A | 2024-11 | 46 | 4.40 | 1.70 | 2.67 |
| new dev-A | 2024-12 | 43 | -5.34 | 1.92 | -7.31 |
| new dev-A | 2025-01 | 14 | -2.59 | 0.63 | -3.21 |

The first two months contain the same 89 entries in both runs (46 in
November, 43 in December), so they are a clean comparison. The new model
is 2.9 USDT worse in gross there, while explicit slippage over the whole
run is 3.30 USDT. In January the new model hit the kill switch after 14
of the month's trades; the old model kept trading and its later months
produced the positive gross that the old report showed. The old +2.79
is therefore not an edge that the realistic model lost to fees: it is a
later, luckier slice of the path that the realistic model did not
survive. The realistic model ruined in three months, not seven.

## Trading window

| run | first_entry | last_entry | entries | final_equity |
|---|---|---|---|---|
| old dev-A | 2024-11-02 | 2025-05-20 | 240 | 12.44 |
| new dev-A | 2024-11-02 | 2025-01-19 | 103 | 12.13 |
| old dev-B | 2026-01-05 | 2026-04-27 | 82 | 10.20 |
| new dev-B | 2026-01-05 | 2026-03-31 | 55 | 10.18 |

Both runs hit the 50% peak-to-trough kill switch and then stop opening
positions: the new model in January 2025, the old model in May 2025.
The remaining months of each window are flat. Metrics are therefore
`until ruin`, not full-window performance. This is the single most
important baseline fact.

## Long and short

| run | side | trades | net_pnl | win_rate | expectancy_r |
|---|---|---|---|---|---|
| old dev-A | long | 129 | -14.99 | 0.50 | -0.11 |
| old dev-A | short | 111 | 7.45 | 0.64 | 0.07 |
| new dev-A | long | 56 | -7.74 | 0.52 | -0.14 |
| new dev-A | short | 47 | -0.12 | 0.62 | -0.02 |
| old dev-B | long | 55 | -8.27 | 0.44 | -0.19 |
| old dev-B | short | 27 | -1.55 | 0.52 | -0.06 |
| new dev-B | long | 37 | -7.62 | 0.43 | -0.26 |
| new dev-B | short | 18 | -2.21 | 0.44 | -0.16 |

## Benchmarks on the development subset

| variant | trades | net_pnl | expectancy_r | profit_factor | win_rate | max_drawdown_pct |
|---|---|---|---|---|---|---|
| trend_pullback_benchmark | 39 | -6.30 | -0.16 | 0.79 | 0.36 | 50.22 |
| random_entry_benchmark | 23 | -8.99 | -0.49 | 0.43 | 0.26 | 50.24 |

The random and trend benchmarks use the 12-symbol development subset,
the same cost model and the same stop/target machinery.

## Cost stress (commission x1.5, slippage x2.0)

| run | trades | net_pnl | profit_factor | expectancy_r | max_drawdown_pct | fees | entry_slippage |
|---|---|---|---|---|---|---|---|
| normal | 103 | -7.85 | 0.77 | -0.08 | 50.45 | 4.25 | 1.04 |
| stress | 101 | -7.78 | 0.76 | -0.09 | 50.21 | 6.13 | 1.95 |

Period: `2024-11-01` .. `2025-12-31`, 40 symbols.

The normal stress run reproduces the validation champion exactly (103
trades, -7.85 USDT), which confirms the cost model is deterministic.
`entry_slippage` is the metrics field (entry fills only); total adverse
price movement is 3.30 USDT in the cost decomposition above. The stress
multipliers raise fees from 4.25 to 6.13 and push the outcome marginally
worse, but the account ruins in both cases, so the difference is small.

## Gate status

Internal validation verdict: **NO_GO**.

Failed criteria: oos_trades_ge_100, oos_profit_factor_gt_1_2, oos_positive_expectancy, oos_max_dd_le_30pct, walk_forward_majority_positive, sensitivity_majority_positive, beats_trend_pullback, positive_ci_lower_bound

The frozen gates in `config/gates.yaml` govern the holdout and are not
applied to the development baseline. The baseline is a reference point,
not a GO candidate.

## Run resources

* wall clock: 1,360.7 s (22.7 min)
* peak RSS: 1.71 GiB (four validation workers)
* champion dev: 24,436,800 1m bars in 421.2 s = 58,017 bars/s

## Reproduce

```bash
uv run bot validate --symbols universe --start 2024-11-01 \
  --dev-end 2025-12-31 --oos-end 2026-06-30 --vault-end 2026-10-20 --workers 4
uv run python scripts/run_stress.py
```
