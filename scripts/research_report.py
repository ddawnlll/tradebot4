"""Render the research round 1 report from the machine-readable artifacts."""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "reports" / "research_round1"


def load(name: str) -> dict:
    return json.loads((OUT / name).read_text())


def f(value, nd: int = 4) -> str:
    if value is None:
        return "-"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, int):
        return str(value)
    try:
        return f"{float(value):,.{nd}f}"
    except Exception:
        return str(value)


def table(rows: list[dict], cols: list[str]) -> str:
    head = "| " + " | ".join(cols) + " |"
    sep = "|" + "|".join("---" for _ in cols) + "|"
    body = ["| " + " | ".join(str(r.get(c, "-")) for c in cols) + " |" for r in rows]
    return "\n".join([head, sep, *body])


def main() -> None:
    summary = load("summary.json")
    gates = load("gates.json")
    baseline_run = json.loads((ROOT / "reports" / "baseline_costed" / "baseline.json").read_text())
    base = summary["baseline"]
    rnd = summary["random"]
    h1 = summary["hypotheses"]["H1"]
    h2 = summary["hypotheses"]["H2"]
    h3 = summary["hypotheses"]["H3"]

    md: list[str] = []
    md += [
        "# Research round 1: cost hypotheses on the frozen development split",
        "",
        "Status: **NO_GO**. No cost hypothesis passes the frozen hypothesis gates.",
        "The holdout (2026-07-01 .. 2026-10-20) was **not opened**.",
        "",
        "## 1. What was tested",
        "",
        "The round uses the development split frozen in `config/data_split.yaml`:",
        "2024-11-01 .. 2026-06-30, 40 symbols, 1m execution with 15m/1h/4h",
        "aggregation. The holdout is sealed. The gates in `config/gates.yaml` were",
        "frozen before the first run. Each hypothesis is capped at six",
        "configurations and evaluated on four walk-forward folds of five months",
        "each, 20 symbols, starting from a fresh 20 USDT account per fold.",
        "",
        "The explicit cost model (ADR 0007) charges market `max(2 bps, 1 tick)`",
        "adverse, stop 5 bps adverse plus the bar gap, limit fills only after one",
        "tick through the price with 90% touch probability, and maker/taker VIP0",
        "fees. The cost stress multiplies commission by 1.5 and slippage by 2.0.",
        "",
        "## 2. Baseline",
        "",
        "### 2.1 Official validation, one run",
        "",
    ]
    b = baseline_run
    rows = []
    for label, src in (("old model / dev-A", b["old"]["dev"]), ("new model / dev-A", b["new"]["dev"]),
                       ("old model / dev-B", b["old"]["oos"]), ("new model / dev-B", b["new"]["oos"])):
        rows.append({"name": label, "trades": src.get("trades"), "gross": f(src.get("gross_pnl"), 2),
                     "slippage": f(src.get("slippage"), 2), "fees": f(src.get("fees"), 2),
                     "funding": f(src.get("funding"), 2), "net": f(src.get("net_pnl"), 2),
                     "pf": f(src.get("profit_factor"), 3), "exp": f(src.get("expectancy_r"), 4),
                     "dd": f(src.get("max_drawdown_pct"), 1)})
    md += [table(rows, ["name", "trades", "gross", "slippage", "fees", "funding", "net", "pf", "exp", "dd"]), ""]
    md += [
        "`dev-A` is 2024-11-01..2025-12-31; `dev-B` is the old out-of-sample",
        "window 2026-01-01..2026-06-30, which is development data under the frozen",
        "split. The old model had `prob_slippage=0` and a 100% touch fill; the new",
        "model is the ADR 0007 model. The new dev-A run produced 103 trades and",
        "hit the 50% peak-to-trough kill switch on 2025-01-19; the old model",
        "reached its kill switch on 2025-05-20 after 240 trades. Metrics are",
        "therefore `until ruin`, not full-window performance.",
        "",
        "The cost decomposition of the new dev-A run: gross before slippage "
        f"{f(b['new_costs']['dev']['gross_before_slippage'], 2)} USDT, slippage "
        f"{f(b['new_costs']['dev']['slippage_usdt'], 2)}, fees "
        f"{f(b['new_costs']['dev']['fees'], 2)}, funding "
        f"{f(b['new_costs']['dev']['funding'], 2)}, net "
        f"{f(b['new_costs']['dev']['net_pnl'], 2)}. The round trip costs "
        "0.07-0.09 USDT, or 0.35-0.45% of starting equity per trade. The old "
        "gross +2.79 was a later, luckier slice of a path the realistic model "
        "did not survive; the first two months contain the same 89 entries and "
        "the new model is 2.9 USDT worse in gross there.",
        "",
        "### 2.2 Four-fold baseline used for the hypothesis comparison",
        "",
        f"* trades {base['trades']}, net {f(base['net_pnl'], 2)} USDT, PF {f(base['profit_factor'], 3)}, expectancy {f(base['expectancy_r'], 4)}R",
        f"* 90% CI of mean R: [{f(base['ci90'][0], 4)}, {f(base['ci90'][1], 4)}]",
        f"* positive folds {base['positive_folds']}/4; fold net {base['fold_net']}",
        f"* random-entry benchmark: trades {rnd['trades']}, net {f(rnd['net_pnl'], 2)}, PF {f(rnd['profit_factor'], 3)}, expectancy {f(rnd['expectancy_r'], 4)}R, positive folds {rnd['positive_folds']}/4",
        "",
        "The baseline is **worse than random entries** on expectancy and PF. The",
        "range filters are not adding value relative to a seeded random entry with",
        "the same stops, targets, sizing and cost model. The dominant loss driver",
        "is the breakout exit: in the single dev-A run, 42 breakout exits lost",
        "30.68 USDT, while TP2 plus breakeven-stop wins earned 25.42 USDT.",
        "",
        "## 3. Hypotheses",
        "",
        "### H1: post-only limit at the zone edge",
        "",
        "The entry is a post-only limit at the inner edge of the zone, kept for",
        "N bars and cancelled if unfilled. Stops, targets, sizing and the time",
        "stop are unchanged.",
        "",
    ]
    h1_rows = []
    for cid, a in h1["configs"].items():
        h1_rows.append({"config": cid, "trades": a["trades"], "net": f(a["net_pnl"], 2),
                        "pf": f(a["profit_factor"], 3), "exp": f(a["expectancy_r"], 4),
                        "ci90": f"[{f(a['ci90'][0], 4)}, {f(a['ci90'][1], 4)}]",
                        "folds +": f"{a['positive_folds']}/4", "fill": f(a["fill_rate"], 3),
                        "cancel": a["cancel_signals"], "reject": a.get("rejected_entries", 0)})
    for row, cid in zip(h1_rows, h1["configs"], strict=False):
        row["fold net"] = ", ".join(f(x, 2) for x in h1["configs"][cid].get("fold_net", []))
    md += [table(h1_rows, ["config", "trades", "net", "fold net", "pf", "exp", "ci90", "folds +", "fill", "cancel", "reject"]), ""]
    adv = load("h1_adverse_selection.json")
    adv_rows = []
    for label in ("filled", "cancelled"):
        a = adv[label]
        adv_rows.append({"group": label, "n": a["n"],
                         "fwd_close": f"{100 * a['mean_fwd_close']:+.2f}%" if a["mean_fwd_close"] is not None else "-",
                         "ci_close": f"[{100 * a['ci90_fwd_close'][0]:+.2f}%, {100 * a['ci90_fwd_close'][1]:+.2f}%]",
                         "fwd_entry": f"{100 * a['mean_fwd_entry']:+.2f}%" if a["mean_fwd_entry"] is not None else "-"})
    md += [table(adv_rows, ["group", "n", "fwd_close", "ci_close", "fwd_entry"]), "",
           "`fwd_entry` is measured from the limit price. For a cancelled entry this",
           "is a counterfactual: the price never reached the limit, so the entry",
           "would not have happened. The comparison is only a measure of what the",
           "cancelled limit would have captured.", ""]
    md += [
        "Harness note: the first H1 pass aborted 9 of 16 folds because the",
        "`strict_errors` guard treats an expected post-only rejection as fatal.",
        "Those failed trials are in the ledger. H1 was rerun with",
        "`strict_errors=False`; the 16 completed folds are the result below.",
        "",
        "H1a (N=4, edge) is the best H1 configuration by expectancy and PF: 678",
        "trades, PF 0.919, expectancy -0.0296R, 90% CI [-0.0767, +0.0160], one",
        "positive fold out of four. The fill rate is 65.4%; 353 limits were",
        "cancelled and 6 were rejected as post-only because the market was",
        "already at the limit. Under cost stress H1a has 606 trades, PF 0.860 and",
        "expectancy -0.0507R.",
        "",
        "Adverse selection is present but is not the main problem. Over the eight",
        "hours after the signal, filled entries moved -0.26% from the signal close",
        "(90% CI [-0.50%, -0.02%]); cancelled entries moved +0.07% (CI [-0.22%,",
        "+0.38%]). The filled-minus-cancelled difference is -0.33% (90% CI",
        "[-0.71%, +0.04%]), i.e. the limits that fill are the slightly worse ones,",
        "at the edge of significance. The bigger issue is that even the entry",
        "price improvement is too small to turn a negative gross edge positive.",
        "",
        "### H2: stricter range width to cost ratio",
        "",
    ]
    h2_rows = []
    for cid, a in h2["configs"].items():
        h2_rows.append({"config": cid, "trades": a["trades"], "net": f(a["net_pnl"], 2),
                        "pf": f(a["profit_factor"], 3), "exp": f(a["expectancy_r"], 4),
                        "ci90": f"[{f(a['ci90'][0], 4)}, {f(a['ci90'][1], 4)}]",
                        "folds +": f"{a['positive_folds']}/4"})
    md += [table(h2_rows, ["config", "trades", "net", "pf", "exp", "ci90", "folds +"]), ""]
    md += [
        "Raising `min_width_cost_ratio` from 3.0 to 4.0 collapses the sample to 27",
        "trades across four folds; 5.0, 6.0 and 8.0 produce zero trades. The",
        "baseline ratio 3.0 is already at the edge of tradability: the validated",
        "ranges are narrow relative to the round-trip cost. H2 is rejected as",
        "underpowered and negative.",
        "",
        "### H3: 1h entry signals",
        "",
    ]
    h3_rows = []
    for cid, a in h3["configs"].items():
        h3_rows.append({"config": cid, "trades": a["trades"], "net": f(a["net_pnl"], 2),
                        "pf": f(a["profit_factor"], 3), "exp": f(a["expectancy_r"], 4),
                        "ci90": f"[{f(a['ci90'][0], 4)}, {f(a['ci90'][1], 4)}]",
                        "folds +": f"{a['positive_folds']}/4"})
    md += [table(h3_rows, ["config", "trades", "net", "pf", "exp", "ci90", "folds +"]), ""]
    md += [
        "The 1h entry cadence produces 88 trades (32-bar time stop) and 89 trades",
        "(8-bar time stop) across four folds, below the 100-trade floor. The 8-bar",
        "time stop is the least negative H3 configuration (PF 0.884, expectancy",
        "-0.0391R, CI [-0.154, +0.079], two positive folds), second only to H1a",
        "on the point estimate, and under",
        "stress it is PF 0.795 / -0.0702R. It beats the random benchmark but fails",
        "the PF and trade-count gates; it is not a candidate.",
        "",
        "## 4. Gate results",
        "",
        "| hypothesis | best config | trades | PF | exp R | CI90 low | stress PF | plateau | verdict |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for name, block, cfg in (("H1", h1, "h1a"), ("H2", h2, "h2a"), ("H3", h3, "h3b")):
        g = gates["hypotheses"][name]["gates"][cfg]
        a = block["configs"][cfg]
        stress = block.get("stress") or {}
        md.append(
            f"| {name} | {cfg} | {a['trades']} | {f(a['profit_factor'], 3)} | {f(a['expectancy_r'], 4)} | "
            f"{f(a['ci90'][0], 4)} | {f(stress.get('profit_factor'), 3)} | {f(g.get('plateau'), 0)} | "
            f"**{'PASS' if g.get('configuration_verdict') else 'FAIL'}** |"
        )
    md += [
        "",
        "The frozen criteria are: at least 100 walk-forward trades, PF >= 1.10,",
        "PF >= 1.00 under cost stress, 90% bootstrap CI lower bound above the",
        "baseline mean R, beats the random benchmark, and a parameter plateau.",
        "No configuration passes all six. H1a passes the trade count, the CI and",
        "the random test but fails PF, stress PF and plateau. H3b passes the CI",
        "and the random test but fails the trade count, PF, stress PF and plateau.",
        "H2 fails the trade count and PF.",
        "",
        "## 5. Holdout",
        "",
        "The holdout was not opened. No hypothesis produced a candidate that",
        "passed the frozen gates, so no candidate justification was committed and",
        "the one permitted holdout opening was preserved. The validation vault",
        "was skipped because the development OOS segment failed its core criteria;",
        "no run in this round touched 2026-07-01..2026-10-20. The holdout manifest",
        "hash remains `4768406fa559fc4fc0f2124fc946a262`.",
        "",
        "## 6. What this means",
        "",
        "1. The realistic cost model is not the only problem. The gross PnL before",
        "   costs is negative or near zero, the baseline is worse than random, and",
        "   the breakout exit is the largest loss driver. A cheaper entry cannot",
        "   fix a negative gross edge.",
        "2. The strategy ruins. The baseline has zero positive folds and the",
        "   single dev-A run hits the 50% kill switch in three months. H1a has",
        "   one positive fold and H3b two, but both still lose after costs. The",
        "   four-fold baseline is a sequence of four fresh 20 USDT accounts, not",
        "   one account; each fold is truncated by ruin when it hits the limit.",
        "3. The range width/cost filter has no room. The current 3.0 ratio is the",
        "   maximum that still produces a sample; 4.0 leaves 27 trades and 5.0",
        "   leaves none.",
        "4. 1h signals reduce frequency without creating an edge. The 8-bar time",
        "   stop is less bad than 32 bars, but 89 trades and PF 0.884 are not",
        "   enough.",
        "",
        "## 7. Suggested next directions (maximum 3, same family)",
        "",
        "1. **Exit and tail risk.** The 42 breakout exits in dev-A lost 30.68 USDT",
        "   against 25.42 USDT of TP2 plus breakeven wins. Test a preregistered",
        "   exit redesign: invalidate earlier, or replace the breakout close exit",
        "   with the hard stop, with a maximum of six configurations. This is the",
        "   largest single loss driver and the only change with a plausible path",
        "   to a positive gross edge.",
        "2. **Range persistence and quality.** The current score rewards width,",
        "   touches and duration but not whether the range has repeatedly mean-",
        "   reverted. Test a small set of age, touch-quality and volatility-",
        "   compression filters on the development folds.",
        "3. **Stop at 20 USDT.** A round trip costs 0.35-0.45% of starting equity,",
        "   and the observed gross edge is negative. If directions 1 and 2 fail,",
        "   the honest conclusion is that this family is not viable at this",
        "   account size; raising the account is a capital decision, not a",
        "   strategy fix.",
        "",
        "## 8. Artifacts and reproduction",
        "",
        "* `reports/baseline_costed/baseline.md` and `.json`: the costed baseline,",
        "  cost decomposition, stress and resources.",
        "* `reports/research_round1/summary.json`: every fold and aggregate.",
        "* `reports/research_round1/gates.json`: the recomputed frozen gates.",
        "* `reports/research_round1/h1_adverse_selection.json`: fill/adverse",
        "  selection measurement.",
        "* `kaizen/experiments.parquet`: append-only ledger, including the failed",
        "  H1 first pass (see the ledger note).",
        "* `kaizen/hypotheses/HYP-001..003.md`: preregistered hypotheses.",
        "",
        "```bash",
        "uv run bot validate --symbols universe --start 2024-11-01 \\",
        "  --dev-end 2025-12-31 --oos-end 2026-06-30 --vault-end 2026-10-20 --workers 4",
        "uv run python scripts/run_stress.py",
        "uv run python scripts/run_hypothesis.py --hypothesis all --workers 4",
        "uv run python scripts/evaluate_hypotheses.py",
        "uv run python scripts/h1_adverse_selection.py",
        "```",
        "",
    ]
    (OUT / "report.md").write_text("\n".join(md))
    print(f"wrote {OUT/'report.md'}")


if __name__ == "__main__":
    main()
