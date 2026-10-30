# Validation report

Generated: 2026-10-29T21:44:39.661142+00:00

## Champion (dev)

| trades | net_pnl | expectancy_r | profit_factor | win_rate | max_drawdown_pct | return_pct | run_id |
|---|---|---|---|---|---|---|---|
| 240 | -7.538 | -0.026 | 0.902 | 0.562 | 50.248 | -37.789 | 20261029T214439Z_range_v1_72b464dfa0220d97 |

## Walk-forward

| trades | net_pnl | expectancy_r | profit_factor | win_rate | max_drawdown_pct | return_pct | run_id |
|---|---|---|---|---|---|---|---|
| 81 | -0.374 | 0.005 | 0.986 | 0.593 | 25.809 | -1.954 | 20261029T215235Z_range_v1_17b2ee90749ba26c |
| 67 | -7.352 | -0.123 | 0.706 | 0.478 | 50.099 | -36.765 | 20261029T215235Z_range_v1_34bb55f7e8a5b59a |
| 30 | -2.597 | -0.079 | 0.770 | 0.500 | 26.601 | -12.985 | 20261029T215235Z_range_v1_d9e579cb5c4e52e4 |
| 28 | -2.569 | -0.091 | 0.759 | 0.464 | 32.110 | -12.798 | 20261029T215235Z_range_v1_687d27d11d96a78e |

## Sensitivity

| variant | trades | net_pnl | expectancy_r | profit_factor | win_rate | max_drawdown_pct | return_pct | run_id |
|---|---|---|---|---|---|---|---|---|
| min_score=0.45 | 138 | -9.576 | -0.082 | 0.761 | 0.522 | 0.501 | -47.893 | 20261029T215331Z_range_v1_d65855bc351092f1 |
| min_score=0.55 | 75 | -8.368 | -0.132 | 0.682 | 0.493 | 0.500 | -41.899 | 20261029T215331Z_range_v1_d2d7d519c6898fa7 |
| min_score=0.65 | 27 | -8.459 | -0.395 | 0.287 | 0.333 | 0.423 | -42.287 | 20261029T215331Z_range_v1_5733e647e42569ab |
| stop_buffer_atr=0.25 | 75 | -8.368 | -0.132 | 0.682 | 0.493 | 0.500 | -41.899 | 20261029T215331Z_range_v1_e932a68dec82c398 |
| stop_buffer_atr=0.45 | 75 | -8.368 | -0.132 | 0.682 | 0.493 | 0.500 | -41.899 | 20261029T215511Z_range_v1_c38b642bd0f532b4 |
| rejection_wick_frac=0.15 | 110 | -8.860 | -0.096 | 0.749 | 0.527 | 0.504 | -44.369 | 20261029T215515Z_range_v1_d5a39bd28b2c0de6 |
| rejection_wick_frac=0.35 | 79 | -6.077 | -0.079 | 0.771 | 0.532 | 0.428 | -30.431 | 20261029T215516Z_range_v1_b49facf38628db06 |
| time_stop_bars=24 | 77 | -8.421 | -0.129 | 0.684 | 0.494 | 0.503 | -42.162 | 20261029T215537Z_range_v1_be4fb66b7ee819b2 |
| time_stop_bars=48 | 75 | -8.532 | -0.135 | 0.679 | 0.507 | 0.507 | -42.719 | 20261029T215717Z_range_v1_af062645fba69dce |
| breakout_exit_atr=0.25 | 89 | -8.007 | -0.104 | 0.729 | 0.494 | 0.504 | -40.101 | 20261029T215741Z_range_v1_e315ebf198dbb7fc |
| breakout_exit_atr=0.5 | 72 | -7.840 | -0.127 | 0.714 | 0.514 | 0.501 | -39.263 | 20261029T215748Z_range_v1_bc779c525c649128 |

## Ablations and benchmarks

| variant | trades | net_pnl | expectancy_r | profit_factor | win_rate | max_drawdown_pct | return_pct | run_id |
|---|---|---|---|---|---|---|---|---|
| regime_filter_off | 174 | -9.127 | -0.058 | 0.832 | 0.540 | 0.502 | -45.595 | 20261029T215941Z_range_v1_49a6f7481f5b1e14 |
| higher_tf_filter_off | 90 | -8.250 | -0.104 | 0.738 | 0.511 | 0.504 | -41.288 | 20261029T215941Z_range_v1_e17b3a83077fad83 |
| breakout_exit_off | 71 | -8.308 | -0.137 | 0.697 | 0.535 | 0.503 | -41.603 | 20261029T215941Z_range_v1_7e81f1953a298070 |
| single_tp | 75 | -8.371 | -0.132 | 0.682 | 0.493 | 0.500 | -41.913 | 20261029T215941Z_range_v1_db3bada93c549d17 |
| trend_pullback_benchmark | 42 | -4.270 | -0.078 | 0.864 | 0.333 | 0.501 | -21.368 | 20261029T220126Z_trend_pullback_d9d65c9eb9074bd3 |
| random_entry_benchmark | 244 | 3.855 | 0.056 | 1.021 | 0.389 | 0.502 | 19.522 | 20261029T220127Z_random_entry_310232cc8f0edd6a |

## Out of sample

| trades | net_pnl | expectancy_r | profit_factor | win_rate | max_drawdown_pct | return_pct | run_id |
|---|---|---|---|---|---|---|---|
| 82 | -9.811 | -0.151 | 0.621 | 0.463 | 50.511 | -49.017 | 20261029T220347Z_range_v1_b0066899b73e6155 |

## Monte Carlo (iid / block)

```json
{
  "iid": {
    "paths": 10000,
    "horizon_trades": 240,
    "prob_target": 0.0523,
    "prob_ruin": 0.4738,
    "prob_end_above_start": 0.1992,
    "median_final_equity": 12.501802790714105,
    "p05_final_equity": 5.192944249478029,
    "p95_final_equity": 31.172504291332892,
    "median_max_drawdown_pct": 56.64434639101209,
    "p95_max_drawdown_pct": 78.49849618527597,
    "median_trades_to_target": 143.0,
    "median_trades_to_ruin": 142.0,
    "block_bootstrap": false
  },
  "block": {
    "paths": 10000,
    "horizon_trades": 240,
    "prob_target": 0.0589,
    "prob_ruin": 0.4718,
    "prob_end_above_start": 0.2061,
    "median_final_equity": 12.68766216100905,
    "p05_final_equity": 5.087498530289066,
    "p95_final_equity": 32.19439324830289,
    "median_max_drawdown_pct": 56.886401793700195,
    "p95_max_drawdown_pct": 79.32061783218532,
    "median_trades_to_target": 139.0,
    "median_trades_to_ruin": 139.0,
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
    "beats_random_entry": false,
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
    "beats_random_entry",
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
