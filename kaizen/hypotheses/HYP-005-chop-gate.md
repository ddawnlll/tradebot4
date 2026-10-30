# HYP-005: Chop / no-trade filters

Status: diagnostic screen only. A1 is DATA_BLOCKED.

## Candidates

* A1 cost/excursion: expected gross excursion must be a causal, fold-safe
  estimate. The only excursion available is future MFE, so A1 is not evaluated.
* A2 failure cooldown: no new same-side trade for 8 entry bars after a failed
  same-symbol campaign.
* A3 compression: ATR14 / median ATR14(50) >= 0.65 on closed 15m bars.
* A5: A2 + A3 (not the full v8 composite, because A1 is blocked).

## Criteria

Diagnostic only: count kept/vetoed trades and net R on the conditional
baseline set. A better conditional subset is not portfolio evidence and is not
promoted without a full replay. The 0.65 and 8-bar values are candidate
assumptions, not validated optima.

## Result

See `reports/research/v8_transfer/chop_scan.md`. A2 reduced the set to 99
trades and made mean R worse (-0.1086 vs -0.0838); A3 vetoed none at 0.65; A5
equalled A2. A1 remains blocked. No full replay is proposed.
