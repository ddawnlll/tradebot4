"""Recompute the frozen hypothesis gates from the walk-forward summary."""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SUMMARY = ROOT / "reports" / "research_round1" / "summary.json"
OUT = ROOT / "reports" / "research_round1" / "gates.json"

NEIGHBORS = {
    "H1": {"h1a": ["h1b", "h1c", "h1d"], "h1b": ["h1a"], "h1c": ["h1a"], "h1d": ["h1a"]},
    "H2": {"h2a": ["h2b"], "h2b": ["h2a", "h2c"], "h2c": ["h2b", "h2d"], "h2d": ["h2c"]},
    "H3": {"h3a": ["h3b"], "h3b": ["h3a"]},
}


def main() -> None:
    summary = json.loads(SUMMARY.read_text())
    baseline = summary["baseline"]
    random_agg = summary["random"]
    out = {"baseline": baseline, "random": random_agg, "hypotheses": {}}
    for name, block in summary["hypotheses"].items():
        configs = block["configs"]
        gates = {}
        eligible = []
        for cid, agg in configs.items():
            neighbors = [configs[n] for n in NEIGHBORS.get(name, {}).get(cid, []) if n in configs]
            positive_neighbors = sum(1 for n in neighbors if (n.get("expectancy_r") or -99) > 0)
            plateau = bool(neighbors) and positive_neighbors * 2 >= len(neighbors)
            gate = {
                "trades_ge_100": agg["trades"] >= 100,
                "pf_ge_1_10": bool(agg["profit_factor"] and agg["profit_factor"] >= 1.10),
                "ci90_above_baseline": bool(
                    agg["ci90"][0] is not None
                    and baseline.get("expectancy_r") is not None
                    and agg["ci90"][0] > baseline["expectancy_r"]
                ),
                "beats_random": bool(
                    agg.get("expectancy_r") is not None
                    and random_agg.get("expectancy_r") is not None
                    and agg["expectancy_r"] > random_agg["expectancy_r"]
                ),
                "plateau": plateau,
                "positive_neighbors": positive_neighbors,
                "neighbors": len(neighbors),
            }
            if cid == block["best"] and block.get("stress"):
                gate["pf_stress_ge_1_00"] = bool(
                    block["stress"].get("profit_factor") and block["stress"]["profit_factor"] >= 1.00
                )
            gate["configuration_verdict"] = all(
                gate[k] for k in ("trades_ge_100", "pf_ge_1_10", "ci90_above_baseline", "beats_random", "plateau")
            )
            gates[cid] = gate
            if gate["configuration_verdict"]:
                eligible.append(cid)
        out["hypotheses"][name] = {
            "gates": gates,
            "best": block["best"],
            "best_verdict": gates.get(block["best"], {}).get("configuration_verdict", False)
            and gates.get(block["best"], {}).get("pf_stress_ge_1_00", True),
            "eligible": eligible,
            "stress": block.get("stress"),
        }
    OUT.write_text(json.dumps(out, indent=2, default=str))
    for name, block in out["hypotheses"].items():
        print(name, "best", block["best"], "best_verdict", block["best_verdict"], "eligible", block["eligible"])
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
