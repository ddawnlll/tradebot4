"""Diagnostic screen for v8-core chop/no-trade candidates."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from bot.data.storage import Paths
from bot.kaizen.experiments import record_experiment
from bot.research.chop_scan import render_markdown, scan

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "reports" / "research" / "v8_transfer"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-dir", default=None)
    args = ap.parse_args()
    run_dir = Path(args.run_dir) if args.run_dir else Path(
        json.loads((ROOT / "reports" / "validation_20261030T115653Z" / "validation.json").read_text())["champion_dev"]["run_dir"]
    )
    OUT.mkdir(parents=True, exist_ok=True)
    result = scan(run_dir, Paths())
    (OUT / "chop_scan.json").write_text(json.dumps(result, indent=2, default=str))
    (OUT / "chop_scan.md").write_text(render_markdown(result))
    for key in ("baseline", "A2", "A3", "A5_A2_A3"):
        a = result[key]
        record_experiment({
            "round": "v8_transfer", "phase": "chop_scan", "hypothesis": key,
            "branch": "main", "data_slice": "development_conditional_baseline",
            "symbols": 40, "config_hash": result.get("source_config_hash"),
            "run_id": result.get("source_run_id"),
            "params_json": {"filter": key, "source_run": result.get("source_run_dir")},
            "metrics_json": a, "notes": "diagnostic conditional filter; no portfolio replay",
        })
    print(f"wrote {OUT/'chop_scan.md'}")
    for key in ("baseline", "A2", "A3", "A5_A2_A3"):
        a = result[key]
        print(key, a["n"], round(a.get("mean_r") or 0, 4), a.get("pf"))


if __name__ == "__main__":
    main()
