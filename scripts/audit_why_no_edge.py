"""Regenerate the living loss audit from the latest validation report."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from bot.audit.why import append_history, audit_run, render_markdown, write_audit_report

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--validation", default=None)
    ap.add_argument("--doc", default=str(ROOT / "docs" / "audits" / "why_no_edge.md"))
    ap.add_argument("--history", default=str(ROOT / "kaizen" / "audit_history.parquet"))
    ap.add_argument("--run-dir", default=None)
    ap.add_argument("--source", default=None)
    ap.add_argument("--balance", type=float, default=20.0)
    args = ap.parse_args()
    if args.run_dir:
        audit = audit_run(args.run_dir, starting_balance=args.balance)
        audit["strategy"] = Path(args.run_dir).name.split("_")[1] if "_" in Path(args.run_dir).name else ""
        src = args.source or Path(args.run_dir).name
        history = append_history(audit, args.history, src, "NO_GO")
        Path(args.doc).parent.mkdir(parents=True, exist_ok=True)
        Path(args.doc).write_text(render_markdown(audit, history, verdict="NO_GO"))
        print(f"source {args.run_dir}")
        print(f"trades {audit['trades']} net {audit['net']:.2f} PF {audit['pf']} exp {audit['expectancy_r']}")
        print(f"wrote {args.doc}")
        return
    if args.validation:
        path = Path(args.validation)
    else:
        files = sorted((ROOT / "reports").glob("validation_*/validation.json"), key=lambda p: p.stat().st_mtime)
        path = files[-1]
    report = json.loads(path.read_text())
    audit = write_audit_report(report, root=ROOT, doc_path=args.doc, history_path=args.history,
                               source=path.parent.name)
    print(f"source {path}")
    print(f"trades {audit['trades']} net {audit['net']:.2f} PF {audit['pf']} exp {audit['expectancy_r']}")
    print(f"top loss {audit['top_loss_reason']} {audit['top_loss_usdt']:.2f} share {audit['top_loss_share']:.1%}")
    print(f"wrote {args.doc}")


if __name__ == "__main__":
    main()
