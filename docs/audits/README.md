# Continuous loss audit

`why_no_edge.md` is the living answer to "why does the system lose?". It is
regenerated after every `bot validate` run and by the standalone command:

```bash
uv run python scripts/audit_why_no_edge.py
# or audit one run directory
uv run python scripts/audit_why_no_edge.py --run-dir runs/<run_id> --source manual
```

`kaizen/audit_history.parquet` is the append-only history: one row per audited
run, with the cost waterfall, net, PF, expectancy, drawdown, top loss channel,
long/short split and verdict. Re-running the audit for the same run does not
duplicate the row.

The audit is mechanical accounting, not a counterfactual simulation. The
"arithmetic levers" table is labeled as such: it shows what net PnL would be if
a cost or loss component were removed, not what a strategy change would earn.
A real fix still needs a preregistered change and a new walk-forward run.
