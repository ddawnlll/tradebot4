"""Run the conditional exit-policy diagnostic on a baseline run."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from bot.research.exit_scan import render_markdown, scan

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-dir", default=None)
    ap.add_argument("--out", default=str(ROOT / "reports" / "research" / "v8_transfer"))
    ap.add_argument("--horizon-hours", type=float, default=168.0)
    args = ap.parse_args()
    run_dir = Path(args.run_dir) if args.run_dir else Path(
        json.loads((ROOT / "reports" / "validation_20261030T115653Z" / "validation.json").read_text())["champion_dev"]["run_dir"]
    )
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    result = scan(run_dir, horizon_ms=int(args.horizon_hours * 3_600_000))
    (out / "exit_scan.json").write_text(json.dumps(result, indent=2, default=str))
    (out / "exit_scan.md").write_text(render_markdown(result))
    print(f"wrote {out/'exit_scan.md'}")
    for arm in ("baseline_actual", "no_breakout", "stop_only", "chandelier_2.5", "be_1.0", "breakout_0.0", "breakout_0.6"):
        a = result["arms"][arm]
        print(f"{arm:16s} n={a['n']:3d} mean_r={a['mean_r'] if a['mean_r'] is not None else 0:+.4f} pf={a['pf']} dd={a['max_dd_r']:.2f}")


if __name__ == "__main__":
    main()
