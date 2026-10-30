# Feature separation diagnostic

Source: `./runs/20261030T115653Z_range_v1_dacaa5eb69700faa` (run `20261030T115653Z_range_v1_dacaa5eb69700faa`), 103 entries with features.
Descriptive only: AUC vs win and vs BREAKOUT; no model, no filter, no threshold.

| feature | n | coverage | AUC win | AUC breakout | median win | median loss |
|---|---:|---:|---:|---:|---:|---:|
| body_range_ratio | 103 | 1.00 | 0.651 | 0.319 | 0.37223326319070793 | 0.267326732673268 |
| cmf_20 | 103 | 1.00 | 0.644 | 0.364 | 0.01828119207371865 | -0.031158640893579306 |
| inside_bar | 103 | 1.00 | 0.432 | 0.578 | 0.0 | 0.0 |
| real_body | 103 | 1.00 | 0.547 | 0.424 | 0.006199999999999928 | 0.0023999999999999577 |
| bb_bandwidth | 103 | 1.00 | 0.534 | 0.441 | 0.0364790512222065 | 0.034054897825993614 |
| atr_2sigma_active | 103 | 1.00 | 0.457 | 0.551 | 0.0 | 0.0 |
| vol_zscore_100 | 103 | 1.00 | 0.526 | 0.464 | 0.03512328128986558 | -0.07219585850750858 |
| close_position | 103 | 1.00 | 0.461 | 0.523 | 0.5175097890615169 | 0.5500000000000014 |
| bb_lower | 103 | 1.00 | 0.498 | 0.479 | 1.3674395155759194 | 1.2902814616069325 |
| window_low_10 | 103 | 1.00 | 0.498 | 0.479 | 1.3666 | 1.2971 |
| window_low_20 | 103 | 1.00 | 0.498 | 0.479 | 1.3666 | 1.2829 |
| window_low_50 | 103 | 1.00 | 0.498 | 0.479 | 1.36565 | 1.2829 |
| swing_low_20 | 103 | 1.00 | 0.498 | 0.479 | 1.3684500000000002 | 1.2894 |
| window_high_20 | 103 | 1.00 | 0.497 | 0.480 | 1.41585 | 1.322 |
| bb_mid | 103 | 1.00 | 0.497 | 0.480 | 1.3900325 | 1.30588 |
| bb_upper | 103 | 1.00 | 0.497 | 0.480 | 1.4126254844240806 | 1.3214785383930674 |
| window_high_10 | 103 | 1.00 | 0.497 | 0.480 | 1.41585 | 1.322 |
| swing_low_10 | 103 | 1.00 | 0.497 | 0.480 | 1.3689 | 1.2829 |
| prior_low | 103 | 1.00 | 0.497 | 0.480 | 1.1907999999999999 | 1.0561 |
| swing_high_20 | 103 | 1.00 | 0.496 | 0.481 | 1.44685 | 1.3666 |

Top rows are ranked by distance from AUC 0.5 for BREAKOUT. A univariate separation is not an edge; it is a candidate for one preregistered test.
