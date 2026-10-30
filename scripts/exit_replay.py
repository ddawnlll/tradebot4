"""Full Nautilus replay of the exit-scan finalists on development folds."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import polars as pl
from run_hypothesis import FOLDS, _aggregate, _run_batch

from bot.data.storage import Paths
from bot.kaizen.experiments import record_experiment

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "reports" / "research" / "v8_transfer"

BASELINE = {"id": "baseline", "strategy": "range_v1", "params": {}, "label": "actual TP1/TP2/BE/breakout"}
STATIC = [
    {"id": "static_2r", "strategy": "range_v1", "params": {"enable_breakout_exit": False, "time_stop_bars": 10_000}, "static_target_r": 2.0, "label": "single 2R target, no breakout/time stop"},
    {"id": "static_3r", "strategy": "range_v1", "params": {"enable_breakout_exit": False, "time_stop_bars": 10_000}, "static_target_r": 3.0, "label": "single 3R target, no breakout/time stop"},
]
RANDOM = [
    {"id": "random_actual", "strategy": "random_entry", "params": {"entry_probability": 0.02, "seed": 42}, "label": "random baseline"},
    {"id": "random_2r", "strategy": "random_entry", "params": {"entry_probability": 0.02, "seed": 42}, "static_target_r": 2.0, "label": "random 2R"},
    {"id": "random_3r", "strategy": "random_entry", "params": {"entry_probability": 0.02, "seed": 42}, "static_target_r": 3.0, "label": "random 3R"},
]


def _run_set(specs, symbols, balance, phase, workers, cm=1.0, sm=1.0, notes=""):
    tasks = [
        {"spec": s, "fold": f, "symbols": symbols, "starting_balance": balance,
         "commission_multiplier": cm, "slippage_multiplier": sm}
        for s in specs for f in FOLDS
    ]
    rows = _run_batch(tasks, workers)
    for s in specs:
        spec_rows = [r for r in rows if r["id"] == s["id"]]
        for r in spec_rows:
            record_experiment({
                "round": "v8_transfer",
                "phase": phase,
                "hypothesis": s["id"],
                "branch": "main",
                "data_slice": f"development:{r['fold']}",
                "start_ms": None,
                "end_ms": None,
                "symbols": len(symbols),
                "config_hash": "",
                "run_id": r.get("run_id"),
                "params_json": {**s, "starting_balance": balance, "commission_multiplier": cm, "slippage_multiplier": sm},
                "metrics_json": r.get("summary") or {"error": r.get("error")},
                "notes": f"{phase} {r['fold']} balance={balance}",
            })
    aggregates = {}
    for s in specs:
        ok = [r for r in rows if r["id"] == s["id"] and r.get("summary")]
        if ok:
            aggregates[s["id"]] = _aggregate(ok)
    return rows, aggregates


def _gates(candidate, baseline, random_agg, stress, plateau):
    c, b, r, s = candidate, baseline, random_agg, stress
    gate = {
        "trades_ge_100": c["trades"] >= 100,
        "pf_ge_1_10": bool(c["profit_factor"] and c["profit_factor"] >= 1.10),
        "ci90_above_baseline": bool(c["ci90"][0] is not None and b.get("expectancy_r") is not None and c["ci90"][0] > b["expectancy_r"]),
        "beats_random_same_exit": bool(c.get("expectancy_r") is not None and r.get("expectancy_r") is not None and c["expectancy_r"] > r["expectancy_r"]),
        "pf_stress_ge_1_00": bool(s.get("profit_factor") and s["profit_factor"] >= 1.00),
        "plateau": plateau,
    }
    gate["verdict"] = all(gate.values())
    return gate


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    paths = Paths()
    files = sorted(paths.universe.glob("universe_*.parquet"))
    symbols = pl.read_parquet(files[-1]).filter(pl.col("in_universe")).sort("rank")["symbol"].to_list()[:20]
    workers = 4
    result = {"generated": datetime.now(UTC).isoformat(), "symbols": symbols,
              "folds": [{"start": f[0], "end": f[1]} for f in FOLDS],
              "data_split": "development 2024-11-01..2026-06-30", "holdout_opened": False,
              "notes": "Each fold is a fresh independent account; fold net sums are not one portfolio."}

    _, base20 = _run_set([BASELINE], symbols, 20.0, "exit_replay_baseline", workers)
    _, static20 = _run_set(STATIC, symbols, 20.0, "exit_replay_candidate", workers)
    _, stress20 = _run_set(STATIC, symbols, 20.0, "exit_replay_stress", workers, cm=1.5, sm=2.0)
    _, random20 = _run_set(RANDOM, symbols, 20.0, "exit_replay_random", workers)
    _, base500 = _run_set([BASELINE], symbols, 500.0, "exit_replay_500_baseline", workers)
    _, static500 = _run_set(STATIC, symbols, 500.0, "exit_replay_500_candidate", workers)

    result["scenario_20"] = {"baseline": base20.get("baseline"), "candidates": static20,
                             "stress": stress20, "random": random20}
    result["scenario_500"] = {"baseline": base500.get("baseline"), "candidates": static500}
    gates = {}
    for cid, cand in static20.items():
        gates[cid] = _gates(cand, base20.get("baseline", {}), random20.get(cid.replace("static_", "random_"), {}),
                            stress20.get(cid, {}), bool(cand.get("expectancy_r", -1) > 0 and static20.get("static_3r", {}).get("expectancy_r", -1) > 0))
    result["gates"] = gates
    result["best_20"] = max(static20, key=lambda k: (static20[k].get("expectancy_r") or -99, static20[k].get("trades", 0))) if static20 else None
    (OUT / "exit_replay.json").write_text(json.dumps(result, indent=2, default=str))
    md = ["# Exit-policy full replay", "", f"Generated: {result['generated']}", "",
          "Development folds only; holdout 2026-07-01..2026-10-20 not opened. Each fold is a fresh account.",
          "", "## 20 USDT scenario", "",
          "| run | trades | net | PF | exp R | 90% CI | folds + | stress PF |", "|---|---:|---:|---:|---:|---|---:|---:|"]
    for cid, a in {"baseline": base20.get("baseline"), **static20}.items():
        s = stress20.get(cid, {})
        md.append(f"| {cid} | {a.get('trades')} | {a.get('net_pnl',0):+.2f} | {a.get('profit_factor',0):.3f} | "
                  f"{a.get('expectancy_r',0):+.4f} | [{a.get('ci90',[0,0])[0]:+.4f}, {a.get('ci90',[0,0])[1]:+.4f}] | "
                  f"{a.get('positive_folds')}/4 | {(f'{s.get('profit_factor'):.3f}' if s.get('profit_factor') is not None else '-')} |")
    md += ["", "## Random-entry benchmark (same exit)", "", "| run | trades | net | PF | exp R |", "|---|---:|---:|---:|---:|"]
    for cid, a in random20.items():
        md.append(f"| {cid} | {a.get('trades')} | {a.get('net_pnl',0):+.2f} | {a.get('profit_factor',0):.3f} | {a.get('expectancy_r',0):+.4f} |")
    md += ["", "## 500 USDT scenario (separate, not scaled)", "", "| run | trades | net | PF | exp R |", "|---|---:|---:|---:|---:|"]
    for cid, a in {"baseline": base500.get("baseline"), **static500}.items():
        md.append(f"| {cid} | {a.get('trades')} | {a.get('net_pnl',0):+.2f} | {a.get('profit_factor',0):.3f} | {a.get('expectancy_r',0):+.4f} |")
    md += ["", "## Gates", ""]
    for cid, g in gates.items():
        md.append(f"* `{cid}`: {g}")
    md += ["", "A development pass is at most an **INCONCLUSIVE / GO-candidate**; the sealed holdout was not opened.", ""]
    (OUT / "exit_replay.md").write_text("\n".join(md))
    print(f"wrote {OUT/'exit_replay.md'}")
    print(json.dumps({k: (v.get("expectancy_r"), v.get("profit_factor"), v.get("trades")) for k, v in static20.items()}, default=str))


if __name__ == "__main__":
    main()
