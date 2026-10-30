# Kaizen ledger

`experiments.parquet` is append-only. Every backtest configuration tried in a
research round is recorded, including failed and neighbouring configurations.
The columns are documented in `src/bot/kaizen/experiments.py`.

Rules:

* one row per configuration actually run,
* `trial_number` increases monotonically across the round,
* `params_json` and `metrics_json` are canonical JSON,
* the holdout opening is a row with `data_slice=holdout` and a note,
* do not edit or delete rows; add a correcting row instead.
