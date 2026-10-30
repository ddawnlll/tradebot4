# Validation report

Generated: 2026-10-30T11:56:53.814563+00:00

## Champion (dev)

| trades | net_pnl | expectancy_r | profit_factor | win_rate | max_drawdown_pct | return_pct | run_id |
|---|---|---|---|---|---|---|---|
| 103 | -7.854 | -0.084 | 0.767 | 0.563 | 50.448 | -39.346 | 20261030T115653Z_range_v1_dacaa5eb69700faa |

## Walk-forward

| trades | net_pnl | expectancy_r | profit_factor | win_rate | max_drawdown_pct | return_pct | run_id |
|---|---|---|---|---|---|---|---|
| 80 | -2.076 | -0.014 | 0.923 | 0.600 | 32.866 | -10.474 | 20261030T120532Z_range_v1_ce0f0d374d7e4b0f |
| 63 | -7.330 | -0.130 | 0.691 | 0.492 | 50.148 | -36.658 | 20261030T120532Z_range_v1_b6854cd7ea9f00bc |
| 30 | -4.001 | -0.134 | 0.668 | 0.500 | 31.262 | -20.004 | 20261030T120532Z_range_v1_c7ac72ea668ed61b |
| 28 | -3.316 | -0.121 | 0.695 | 0.464 | 34.263 | -16.534 | 20261030T120532Z_range_v1_f85c743c92765e30 |

## Sensitivity

| variant | trades | net_pnl | expectancy_r | profit_factor | win_rate | max_drawdown_pct | return_pct | run_id |
|---|---|---|---|---|---|---|---|---|
| min_score=0.45 | 100 | -9.633 | -0.118 | 0.680 | 0.520 | 50.220 | -48.159 | 20261030T120706Z_range_v1_8a11d435e686ec41 |
| min_score=0.55 | 73 | -8.926 | -0.149 | 0.662 | 0.493 | 50.528 | -44.688 | 20261030T120706Z_range_v1_9854a47dd08da2c6 |
| min_score=0.65 | 27 | -9.761 | -0.482 | 0.234 | 0.333 | 48.797 | -48.797 | 20261030T120706Z_range_v1_1d9dcbcf8f770a7e |
| stop_buffer_atr=0.25 | 73 | -8.926 | -0.149 | 0.662 | 0.493 | 50.528 | -44.688 | 20261030T120706Z_range_v1_172acdf50f5e41ab |
| stop_buffer_atr=0.45 | 73 | -8.926 | -0.149 | 0.662 | 0.493 | 50.528 | -44.688 | 20261030T120852Z_range_v1_410c0cf0ed695e44 |
| rejection_wick_frac=0.15 | 106 | -8.779 | -0.099 | 0.738 | 0.538 | 50.106 | -43.975 | 20261030T120857Z_range_v1_af8680a540b284f7 |
| rejection_wick_frac=0.35 | 87 | -9.417 | -0.135 | 0.680 | 0.506 | 50.937 | -47.094 | 20261030T120858Z_range_v1_297e84f0c7f33a8d |
| time_stop_bars=24 | 74 | -8.832 | -0.144 | 0.667 | 0.500 | 50.108 | -44.218 | 20261030T120905Z_range_v1_2b43c02e1353dd6d |
| time_stop_bars=48 | 72 | -8.874 | -0.149 | 0.663 | 0.514 | 50.298 | -44.430 | 20261030T121053Z_range_v1_5d97f614b6c2784d |
| breakout_exit_atr=0.25 | 76 | -8.154 | -0.128 | 0.696 | 0.487 | 50.067 | -40.830 | 20261030T121112Z_range_v1_a19d536f73bd0368 |
| breakout_exit_atr=0.5 | 61 | -8.066 | -0.156 | 0.676 | 0.525 | 50.119 | -40.392 | 20261030T121113Z_range_v1_f25e96f94e2edfd3 |

## Ablations and benchmarks

| variant | trades | net_pnl | expectancy_r | profit_factor | win_rate | max_drawdown_pct | return_pct | run_id |
|---|---|---|---|---|---|---|---|---|
| regime_filter_off | 123 | -9.457 | -0.090 | 0.759 | 0.545 | 50.102 | -47.273 | 20261030T121323Z_range_v1_47f8718fc3d61141 |
| higher_tf_filter_off | 86 | -8.235 | -0.108 | 0.726 | 0.523 | 50.179 | -41.207 | 20261030T121323Z_range_v1_bbd6a2ff038ce832 |
| breakout_exit_off | 57 | -8.206 | -0.170 | 0.662 | 0.544 | 50.084 | -41.077 | 20261030T121323Z_range_v1_c21c686f52c5221f |
| single_tp | 73 | -8.926 | -0.149 | 0.662 | 0.493 | 50.528 | -44.688 | 20261030T121323Z_range_v1_5c51d10fa6c9d44f |
| trend_pullback_benchmark | 39 | -6.297 | -0.161 | 0.793 | 0.359 | 50.220 | -31.494 | 20261030T121528Z_trend_pullback_c0b725052d3bddb9 |
| random_entry_benchmark | 23 | -8.990 | -0.494 | 0.427 | 0.261 | 50.240 | -45.008 | 20261030T121529Z_random_entry_90e92076222c0c80 |

## Out of sample

| trades | net_pnl | expectancy_r | profit_factor | win_rate | max_drawdown_pct | return_pct | run_id |
|---|---|---|---|---|---|---|---|
| 55 | -9.823 | -0.228 | 0.502 | 0.436 | 50.525 | -49.078 | 20261030T121703Z_range_v1_b97c3de383c7aee4 |

## Monte Carlo (iid / block)

```json
{
  "iid": {
    "paths": 10000,
    "horizon_trades": 103,
    "prob_target": 0.0013,
    "prob_ruin": 0.3555,
    "prob_end_above_start": 0.0734,
    "median_final_equity": 12.215704941443397,
    "p05_final_equity": 6.7174698273774345,
    "p95_final_equity": 21.439484295178985,
    "median_max_drawdown_pct": 48.14303750512863,
    "p95_max_drawdown_pct": 68.95727444440683,
    "median_trades_to_target": 76.0,
    "median_trades_to_ruin": 76.0,
    "block_bootstrap": false
  },
  "block": {
    "paths": 10000,
    "horizon_trades": 103,
    "prob_target": 0.0013,
    "prob_ruin": 0.3771,
    "prob_end_above_start": 0.0842,
    "median_final_equity": 12.16406901607672,
    "p05_final_equity": 6.707455406131661,
    "p95_final_equity": 22.21124674958458,
    "median_max_drawdown_pct": 49.108336241133,
    "p95_max_drawdown_pct": 69.45819928514155,
    "median_trades_to_target": 77.0,
    "median_trades_to_ruin": 75.0,
    "block_bootstrap": true
  }
}
```

## Go / No-Go

```json
{
  "verdict": "NO_GO",
  "criteria": {
    "oos_trades_ge_100": false,
    "oos_profit_factor_gt_1_2": false,
    "oos_positive_expectancy": false,
    "oos_max_dd_le_30pct": false,
    "walk_forward_majority_positive": false,
    "sensitivity_majority_positive": false,
    "beats_random_entry": true,
    "beats_trend_pullback": false,
    "positive_ci_lower_bound": false
  },
  "failed": [
    "oos_trades_ge_100",
    "oos_profit_factor_gt_1_2",
    "oos_positive_expectancy",
    "oos_max_dd_le_30pct",
    "walk_forward_majority_positive",
    "sensitivity_majority_positive",
    "beats_trend_pullback",
    "positive_ci_lower_bound"
  ]
}
```

## Vault

```json
{
  "skipped": "OOS failed core criteria; vault not opened"
}
```
