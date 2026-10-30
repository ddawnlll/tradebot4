# Chop / no-trade diagnostic

Source: `./runs/20261030T115653Z_range_v1_dacaa5eb69700faa` (run `20261030T115653Z_range_v1_dacaa5eb69700faa`).
**Diagnostic only.** The baseline trade set is filtered conditionally; entries, equity, overlap and the kill switch are not re-simulated.

| filter | n | sum R | mean R | PF | win | delta mean R | vetoed | unknown |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| baseline | 103 | -8.629 | -0.0838 | 0.7634961631936158 | 0.563 | +0.0000 | 0 | 0 |
| A2 | 99 | -10.751 | -0.1086 | 0.6994271370930205 | 0.556 | -0.0248 | 4 | 0 |
| A3 | 103 | -8.629 | -0.0838 | 0.7634961631936158 | 0.563 | +0.0000 | 0 | 0 |
| A5_A2_A3 | 99 | -10.751 | -0.1086 | 0.6994271370930205 | 0.556 | -0.0248 | 0 | 0 |

* A1: `DATA_BLOCKED` — expected gross excursion must be a causal, fold-safe estimate; the only excursion available is future MFE, which cannot be used at entry

* A2: 
* A3: 
* A5 is A2+A3 only; it is not the full v8 composite.

A filter that leaves few trades or improves mean R here is a candidate for a full replay, not evidence of profit.
