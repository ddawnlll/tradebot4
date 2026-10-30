"""Descriptive, pre-decision feature separation on the baseline trade set.

Features are computed only from closed 15m bars at or before each entry. This
is not a model, not a filter and not a trading signal: it reports coverage and
univariate separation so a later preregistered hypothesis can choose one
feature, not 36.
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

from bot.core.features import FEATURE_NAMES, FeatureState
from bot.data.storage import Paths
from bot.kaizen.experiments import record_experiment
from bot.research.chop_scan import _bars_15m
from bot.research.exit_scan import load_cases

MS_15M = 900_000

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "reports" / "research" / "v8_transfer"


def _auc(values: np.ndarray, labels: np.ndarray) -> float | None:
    pos = labels.astype(bool)
    n_pos = int(pos.sum())
    n_neg = int((~pos).sum())
    if n_pos == 0 or n_neg == 0:
        return None
    order = np.argsort(values, kind="mergesort")
    ranks = np.empty(len(values), dtype=float)
    ranks[order] = np.arange(1, len(values) + 1)
    # average ties
    sorted_vals = values[order]
    i = 0
    while i < len(sorted_vals):
        j = i + 1
        while j < len(sorted_vals) and sorted_vals[j] == sorted_vals[i]:
            j += 1
        if j - i > 1:
            ranks[order[i:j]] = (i + 1 + j) / 2.0
        i = j
    rank_sum = float(ranks[pos].sum())
    return (rank_sum - n_pos * (n_pos + 1) / 2.0) / (n_pos * n_neg)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-dir", default=None)
    args = ap.parse_args()
    run_dir = Path(args.run_dir) if args.run_dir else Path(
        json.loads((ROOT / "reports" / "validation_20261030T115653Z" / "validation.json").read_text())["champion_dev"]["run_dir"]
    )
    cases, meta = load_cases(run_dir)
    rows: list[dict] = []
    for case in cases:
        bars = _bars_15m(Paths(), case.symbol, case.entry_ts_ms - 650 * MS_15M, case.entry_ts_ms)
        if len(bars) < 30:
            continue
        state = FeatureState(case.symbol)
        snapshot = None
        for bar in bars:
            snapshot = state.update(bar)
        if snapshot is None:
            continue
        row = {"trade_id": case.trade_id, "net_r": case.actual_net_r,
               "win": 1.0 if case.actual_net_r > 0 else 0.0,
               "breakout": 1.0 if case.actual_exit_reason == "BREAKOUT" else 0.0,
               "side": case.side}
        row.update({k: (float(v) if isinstance(v, (bool, int, float)) else None) for k, v in snapshot.values_only().items()})
        rows.append(row)
    if not rows:
        raise SystemExit("no feature snapshots produced")
    table = []
    for name in FEATURE_NAMES:
        vals = np.array([r[name] for r in rows if r[name] is not None], dtype=float)
        idx = [i for i, r in enumerate(rows) if r[name] is not None]
        if len(vals) < 20:
            continue
        y = np.array([rows[i]["win"] for i in idx], dtype=float)
        b = np.array([rows[i]["breakout"] for i in idx], dtype=float)
        table.append({
            "feature": name, "n": len(vals), "coverage": len(vals) / len(rows),
            "auc_win": _auc(vals, y), "auc_breakout": _auc(vals, b),
            "median_win": float(np.median(vals[y > 0])) if (y > 0).any() else None,
            "median_loss": float(np.median(vals[y == 0])) if (y == 0).any() else None,
        })
    table.sort(key=lambda r: abs((r["auc_breakout"] or 0.5) - 0.5), reverse=True)
    result = {
        "generated": datetime.now(UTC).isoformat(),
        "source_run_dir": str(run_dir),
        "source_run_id": meta.get("run_id"),
        "source_config_hash": meta.get("config_hash"),
        "diagnostic_only": True,
        "entry_set": len(rows),
        "features": table,
        "notes": "Univariate descriptive AUC; no model, no threshold, no feature selection. Small sample and conditional trade set.",
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "feature_ablation.json").write_text(json.dumps(result, indent=2, default=str))
    md = ["# Feature separation diagnostic", "",
          f"Source: `{run_dir}` (run `{meta.get('run_id')}`), {len(rows)} entries with features.",
          "Descriptive only: AUC vs win and vs BREAKOUT; no model, no filter, no threshold.", "",
          "| feature | n | coverage | AUC win | AUC breakout | median win | median loss |",
          "|---|---:|---:|---:|---:|---:|---:|"]
    for r in table[:20]:
        md.append(f"| {r['feature']} | {r['n']} | {r['coverage']:.2f} | {r['auc_win']:.3f} | "
                  f"{r['auc_breakout']:.3f} | {r['median_win']} | {r['median_loss']} |")
    md += ["", "Top rows are ranked by distance from AUC 0.5 for BREAKOUT. A univariate separation is not an edge; it is a candidate for one preregistered test.", ""]
    (OUT / "feature_ablation.md").write_text("\n".join(md))
    record_experiment({
        "round": "v8_transfer", "phase": "feature_ablation", "hypothesis": "marketstate_batch1",
        "branch": "main", "data_slice": "development_conditional_baseline",
        "symbols": 40, "config_hash": result.get("source_config_hash"),
        "run_id": result.get("source_run_id"),
        "params_json": {"features": list(FEATURE_NAMES), "source_run": str(run_dir)},
        "metrics_json": {"entry_set": len(rows), "top_breakout": table[:10]},
        "notes": "descriptive feature separation; no model/filter",
    })
    print(f"wrote {OUT/'feature_ablation.md'} ({len(rows)} entries)")


if __name__ == "__main__":
    main()
