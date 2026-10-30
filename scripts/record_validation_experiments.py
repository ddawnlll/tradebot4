"""Record every configuration from a validation report in the experiment ledger."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from bot.kaizen.experiments import record_experiment


def metrics_of(entry: dict) -> dict:
    if "metrics" in entry:
        m = dict(entry["metrics"])
        m["trades"] = entry.get("trades", m.get("trades"))
        return m
    return {k: v for k, v in entry.items() if k not in ("variant", "run_id")}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("validation_json")
    ap.add_argument("--round", default="research_round1")
    ap.add_argument("--hypothesis", default="baseline")
    ap.add_argument("--data-slice", default="development")
    args = ap.parse_args()
    report = json.loads(Path(args.validation_json).read_text())
    n = 0
    for phase, key in (("baseline_dev", "champion_dev"), ("baseline_oos", "oos"), ("baseline_holdout", "vault")):
        entry = report.get(key)
        if not entry or entry.get("skipped"):
            continue
        record_experiment(
            {
                "round": args.round,
                "phase": phase,
                "hypothesis": args.hypothesis,
                "branch": "eval/cost-fix",
                "data_slice": key,
                "start_ms": report.get("periods", {}).get(key if key != "champion_dev" else "dev", [None, None])[0],
                "end_ms": report.get("periods", {}).get(key if key != "champion_dev" else "dev", [None, None])[1],
                "symbols": len(report.get("base_config", {}).get("symbols", [])),
                "config_hash": entry.get("config_hash"),
                "run_id": entry.get("run_id"),
                "params_json": report.get("base_config", {}).get("strategy_params", {}),
                "metrics_json": metrics_of(entry),
                "notes": f"validation {phase}",
            }
        )
        n += 1
    for phase, key in (("baseline_wf", "walk_forward"), ("baseline_sensitivity", "sensitivity"), ("baseline_ablation", "ablations")):
        for entry in report.get(key, []):
            m = metrics_of(entry)
            record_experiment(
                {
                    "round": args.round,
                    "phase": phase,
                    "hypothesis": args.hypothesis,
                    "branch": "eval/cost-fix",
                    "data_slice": "development",
                    "start_ms": report.get("base_config", {}).get("start_ms"),
                    "end_ms": report.get("base_config", {}).get("end_ms"),
                    "symbols": len(report.get("base_config", {}).get("symbols", [])),
                    "config_hash": entry.get("config_hash") or report.get("base_config", {}).get("strategy_params", {}).__str__(),
                    "run_id": entry.get("run_id"),
                    "params_json": entry.get("variant") or entry.get("config") or {},
                    "metrics_json": m,
                    "notes": f"validation {key}: {entry.get('variant', '')}",
                }
            )
            n += 1
    print(f"recorded {n} validation experiments")


if __name__ == "__main__":
    main()
