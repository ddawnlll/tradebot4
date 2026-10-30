# Exit-policy diagnostic scan

Source: `./runs/20261030T115653Z_range_v1_dacaa5eb69700faa` (run `20261030T115653Z_range_v1_dacaa5eb69700faa`).
**Diagnostic only.** Same baseline_actual_103_trades entries and quantities; future entries, equity, overlap and kill-switch timing are not re-simulated.

| arm | n | sum R | mean R | PF | win | max DD R | delta mean R | ambiguous |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| baseline_actual | 103 | -8.629 | -0.0838 | 0.7634961631936158 | 0.563 | 13.131 | +0.0000 | 0 |
| no_breakout | 103 | -5.878 | -0.0571 | 0.8617664011757659 | 0.505 | 14.967 | +0.0267 | 0 |
| stop_only | 103 | +2.857 | +0.0277 | 1.0498716730113173 | 0.398 | 16.235 | +0.1115 | 0 |
| static_1r | 103 | -1.122 | -0.0109 | 0.9775373005921464 | 0.524 | 14.153 | +0.0729 | 0 |
| static_2r | 103 | +14.949 | +0.1451 | 1.2405203435611767 | 0.408 | 14.389 | +0.2289 | 0 |
| static_3r | 103 | +16.221 | +0.1575 | 1.2246275222668346 | 0.311 | 14.680 | +0.2413 | 0 |
| no_tp | 103 | +2.857 | +0.0277 | 1.0498716730113173 | 0.398 | 16.235 | +0.1115 | 0 |
| chandelier_2.5 | 103 | -19.704 | -0.1913 | 0.65464308458599 | 0.252 | 25.685 | -0.1075 | 0 |
| chandelier_3.0 | 103 | -4.909 | -0.0477 | 0.9149169031988585 | 0.311 | 17.942 | +0.0361 | 0 |
| be_0.5 | 103 | -13.184 | -0.1280 | 0.6428544948340995 | 0.379 | 14.475 | -0.0442 | 0 |
| be_1.0 | 103 | -17.053 | -0.1656 | 0.6761232075847953 | 0.311 | 23.035 | -0.0818 | 0 |
| structural_24h | 103 | +139.767 | +1.3570 | 3.1128934954510528 | 0.204 | 16.986 | +1.4407 | 0 |
| structural_48h | 101 | +168.252 | +1.6659 | 3.2725701189131784 | 0.198 | 20.955 | +1.7496 | 0 |
| breakout_0.0 | 103 | -3.612 | -0.0351 | 0.8845484627696034 | 0.437 | 10.193 | +0.0487 | 0 |
| breakout_0.6 | 103 | -10.603 | -0.1029 | 0.7513182073826336 | 0.437 | 17.900 | -0.0192 | 13 |

## Exit reason mix

* `baseline_actual`: {'TP2': 23, 'STOP_BREAKEVEN': 34, 'BREAKOUT': 42, 'STOP': 2, 'TP1': 1, 'KILL_SWITCH': 1}
* `no_breakout`: {'TP2': 46, 'STOP': 50, 'TIME_STOP': 7}
* `stop_only`: {'TIME_STOP': 49, 'STOP': 54}
* `static_1r`: {'TARGET': 54, 'STOP': 49}
* `static_2r`: {'STOP': 61, 'TARGET': 42}
* `static_3r`: {'STOP': 71, 'TARGET': 32}
* `no_tp`: {'TIME_STOP': 49, 'STOP': 54}
* `chandelier_2.5`: {'STOP': 103}
* `chandelier_3.0`: {'STOP': 103}
* `be_0.5`: {'STOP': 102, 'STOP_GAP': 1}
* `be_1.0`: {'STOP': 103}
* `structural_24h`: {'STOP': 102, 'HORIZON': 1}
* `structural_48h`: {'STOP': 96, 'HORIZON': 5}
* `breakout_0.0`: {'TP2': 35, 'BREAKOUT': 66, 'TIME_STOP': 2}
* `breakout_0.6`: {'TP2': 40, 'BREAKOUT': 42, 'STOP': 15, 'TIME_STOP': 6}

## NOT_RUN

* `structural_48h`: 2

The scan is conditional on the baseline trade set. A better arm here is a candidate for a full Nautilus replay, not evidence of profit.
