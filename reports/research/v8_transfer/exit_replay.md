# Exit-policy full replay

Generated: 2026-10-30T16:15:36.283384+00:00

Development folds only; holdout 2026-07-01..2026-10-20 not opened. Each fold is a fresh account.

## 20 USDT scenario

| run | trades | net | PF | exp R | 90% CI | folds + | stress PF |
|---|---:|---:|---:|---:|---|---:|---:|
| baseline | 253 | -34.77 | 0.625 | -0.1697 | [-0.2479, -0.0938] | 0/4 | - |
| static_2r | 133 | -34.36 | 0.625 | -0.3017 | [-0.4791, -0.1133] | 0/4 | 0.579 |
| static_3r | 108 | -35.53 | 0.537 | -0.3877 | [-0.6184, -0.1402] | 0/4 | 0.466 |

## Random-entry benchmark (same exit)

| run | trades | net | PF | exp R |
|---|---:|---:|---:|---:|
| random_actual | 198 | -29.70 | 0.784 | -0.1482 |
| random_2r | 296 | -6.04 | 0.979 | -0.0020 |
| random_3r | 125 | -27.21 | 0.733 | -0.2244 |

## 500 USDT scenario (separate, not scaled)

| run | trades | net | PF | exp R |
|---|---:|---:|---:|---:|
| baseline | 265 | -840.01 | 0.648 | -0.1527 |
| static_2r | 155 | -851.07 | 0.672 | -0.2505 |
| static_3r | 110 | -887.50 | 0.554 | -0.3764 |

## Gates

* `static_2r`: {'trades_ge_100': True, 'pf_ge_1_10': False, 'ci90_above_baseline': False, 'beats_random_same_exit': False, 'pf_stress_ge_1_00': False, 'plateau': False, 'verdict': False}
* `static_3r`: {'trades_ge_100': True, 'pf_ge_1_10': False, 'ci90_above_baseline': False, 'beats_random_same_exit': False, 'pf_stress_ge_1_00': False, 'plateau': False, 'verdict': False}

A development pass is at most an **INCONCLUSIVE / GO-candidate**; the sealed holdout was not opened.
