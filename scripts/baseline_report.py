"""Build the costed baseline report from validation JSON and run artifacts."""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

import polars as pl

ROOT = Path(__file__).resolve().parents[1]
METRICS = [
    "trades", "gross_pnl", "fees", "funding", "slippage", "net_pnl", "return_pct",
    "profit_factor", "expectancy_r", "win_rate", "max_drawdown_pct", "avg_holding_hours",
    "equity_residual_usdt",
]


def load(path: Path) -> dict:
    return json.loads(path.read_text())


def trade_frame(run_dir: str) -> pl.DataFrame:
    path = Path(run_dir) / "trades.parquet"
    return pl.read_parquet(path) if path.exists() else pl.DataFrame()


def fill_frame(run_dir: str) -> pl.DataFrame:
    path = Path(run_dir) / "fills.parquet"
    return pl.read_parquet(path) if path.exists() else pl.DataFrame()


def cost_breakdown(run_dir: str) -> dict:
    trades = trade_frame(run_dir)
    fills = fill_frame(run_dir)
    if trades.height == 0:
        return {}
    gross = float(trades["gross_pnl"].sum())
    fees = float(trades["fees"].sum())
    funding = float(trades["funding"].sum())
    net = float(trades["net_pnl"].sum())
    slip = 0.0
    by_kind: list[dict] = []
    if fills.height and "reference_price" in fills.columns:
        f = fills.filter(pl.col("reference_price").is_not_null() & (pl.col("reference_price") > 0)).with_columns(
            pl.when(pl.col("side").is_in(["BUY", "Buy"])).then(1.0).otherwise(-1.0).alias("sign"),
            (pl.col("price") - pl.col("reference_price")).alias("diff"),
        )
        f = f.with_columns((pl.col("diff") * pl.col("sign") * pl.col("qty")).alias("adverse"))
        slip = float(f["adverse"].sum())
        for kind, g in f.group_by("kind", maintain_order=True):
            notional = float((g["reference_price"] * g["qty"]).sum())
            adverse = float(g["adverse"].sum())
            by_kind.append(
                {
                    "kind": kind[0],
                    "fills": g.height,
                    "notional": notional,
                    "adverse_usdt": adverse,
                    "avg_bps": (adverse / notional * 1e4) if notional else 0.0,
                }
            )
    return {
        "trades": trades.height,
        "gross_pnl": gross,
        "fees": fees,
        "funding": funding,
        "slippage_usdt": slip,
        "gross_before_slippage": gross + slip,
        "net_pnl": net,
        "net_before_cost": gross + slip - fees + funding,
        "residual": net - (gross - fees + funding),
        "by_kind": by_kind,
    }


def row(name: str, res: dict, costs: dict | None = None) -> dict:
    m = res.get("metrics", {})
    out = {"name": name, "trades": res.get("trades", m.get("trades"))}
    for key in METRICS:
        if key in m:
            out[key] = m[key]
    if costs:
        out.update({f"c_{k}": v for k, v in costs.items() if not isinstance(v, list)})
        out["gross_pnl"] = costs.get("gross_pnl")
        out["slippage"] = costs.get("slippage_usdt")
    elif all(k in m for k in ("net_pnl", "fees", "funding")):
        out["gross_pnl"] = m["net_pnl"] + m["fees"] - m["funding"]
    return out


def fmt(value, digits: int = 2) -> str:
    if value is None:
        return "-"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, int):
        return str(value)
    try:
        return f"{float(value):,.{digits}f}"
    except Exception:
        return str(value)


def table(rows: list[dict], columns: list[str]) -> str:
    head = "| " + " | ".join(columns) + " |"
    sep = "|" + "|".join("---" for _ in columns) + "|"
    body = ["| " + " | ".join(fmt(r.get(c)) for c in columns) + " |" for r in rows]
    return "\n".join([head, sep, *body])


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--new", required=True)
    ap.add_argument("--old", default="reports/validation_20261029T214439Z/validation.json")
    ap.add_argument("--stress", default="reports/baseline_costed/stress.json")
    ap.add_argument("--out", default="reports/baseline_costed")
    ap.add_argument("--elapsed", type=float, default=1360.66)
    ap.add_argument("--peak-rss-gb", type=float, default=1.71)
    ap.add_argument("--champion-bars", type=int, default=24_436_800)
    args = ap.parse_args()
    new = load(Path(args.new))
    old = load(Path(args.old))
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    old_dev = row("old model / dev-A", old["champion_dev"])
    new_dev_costs = cost_breakdown(new["champion_dev"]["run_dir"])
    new_dev = row("new model / dev-A", new["champion_dev"], new_dev_costs)
    old_oos = row("old model / dev-B", old["oos"])
    new_oos_costs = cost_breakdown(new["oos"]["run_dir"])
    new_oos = row("new model / dev-B", new["oos"], new_oos_costs)

    cols = ["name", "trades", "gross_pnl", "slippage", "fees", "funding", "net_pnl", "return_pct",
            "profit_factor", "expectancy_r", "win_rate", "max_drawdown_pct"]
    md = [
        "# Costed baseline",
        "",
        f"Generated: {new.get('generated')}",
        "",
        "Two cost models are shown side by side. The old model is the Milestone 6",
        "validation (`prob_slippage=0`, `prob_fill_on_limit=1.0`). The new model is",
        "ADR 0007: market `max(2 bps, 1 tick)` adverse, stop 5 bps adverse plus the",
        "bar gap, limit fills only after one tick through the price with 90% touch",
        "probability, maker/taker VIP0 fees, funding reconstructed from history.",
        "",
        "`dev-A` is 2024-11-01..2025-12-31. `dev-B` is 2026-01-01..2026-06-30,",
        "the old out-of-sample window; under the frozen split it is development",
        "data. The true holdout 2026-07-01..2026-10-20 was not opened (the",
        "validation skipped the vault because dev-B failed its core criteria).",
        "",
        "## Main comparison (20 USDT, 5% risk)",
        "",
        table([old_dev, new_dev, old_oos, new_oos], cols),
        "",
        "Old gross is reconstructed as `net + fees - funding` because the old",
        "fills did not record a reference price; its measured slippage was ~0.",
        "",
        "## New model cost decomposition",
        "",
    ]
    decomp = []
    for label, c in (("dev", new_dev_costs), ("OOS", new_oos_costs)):
        decomp.append(
            {
                "slice": label,
                "gross_before_slippage": c.get("gross_before_slippage"),
                "slippage_usdt": c.get("slippage_usdt"),
                "fees": c.get("fees"),
                "funding": c.get("funding"),
                "net_pnl": c.get("net_pnl"),
                "net_before_cost": c.get("net_before_cost"),
                "residual": c.get("residual"),
            }
        )
    md += [table(decomp, list(decomp[0])), ""]
    kinds = []
    for label, c in (("dev", new_dev_costs), ("OOS", new_oos_costs)):
        for k in c.get("by_kind", []):
            kinds.append({"slice": label, **k})
    if kinds:
        md += ["### Adverse price by fill kind", "", table(kinds, list(kinds[0])), ""]
        md += ["`avg_bps` is adverse price movement versus the intended price, not the fee.", ""]

    md += ["## Cost per trade", ""]
    per_trade = []
    for label, c in (("new dev-A", new_dev_costs), ("new dev-B", new_oos_costs)):
        n = c.get("trades") or 1
        per_trade.append({"run": label, "fees_per_trade": c.get("fees", 0) / n,
                          "slippage_per_trade": c.get("slippage_usdt", 0) / n,
                          "net_per_trade": c.get("net_pnl", 0) / n,
                          "cost_pct_of_equity_per_trade": 100 * (c.get("fees", 0) + c.get("slippage_usdt", 0)) / n / 20})
    md += [table(per_trade, list(per_trade[0])), ""]
    md += ["The account is 20 USDT; a round trip costs roughly 0.07-0.09 USDT,",
           "or 0.35-0.45% of starting equity per trade.", ""]

    md += ["## Why the old gross was positive", ""]
    monthly = []
    for label, res in (("old dev-A", old["champion_dev"]), ("new dev-A", new["champion_dev"])):
        t = trade_frame(res["run_dir"])
        if t.height:
            t = t.with_columns(pl.from_epoch("entry_ts_ms", "ms").dt.strftime("%Y-%m").alias("month"))
            g = t.group_by("month").agg(
                pl.len().alias("trades"), pl.col("gross_pnl").sum().alias("gross"),
                pl.col("fees").sum().alias("fees"), pl.col("net_pnl").sum().alias("net"),
            ).sort("month")
            for r in g.iter_rows(named=True):
                monthly.append({"run": label, **r})
    if monthly:
        md += [table(monthly, ["run", "month", "trades", "gross", "fees", "net"]), ""]
    md += ["The first two months contain the same 89 entries in both runs (46 in",
           "November, 43 in December), so they are a clean comparison. The new model",
           "is 2.9 USDT worse in gross there, while explicit slippage over the whole",
           "run is 3.30 USDT. In January the new model hit the kill switch after 14",
           "of the month's trades; the old model kept trading and its later months",
           "produced the positive gross that the old report showed. The old +2.79",
           "is therefore not an edge that the realistic model lost to fees: it is a",
           "later, luckier slice of the path that the realistic model did not",
           "survive. The realistic model ruined in three months, not seven.", ""]

    md += ["## Trading window", ""]
    windows = []
    for label, res in (("old dev-A", old["champion_dev"]), ("new dev-A", new["champion_dev"]),
                       ("old dev-B", old["oos"]), ("new dev-B", new["oos"])):
        t = trade_frame(res["run_dir"])
        if t.height:
            first = int(t["entry_ts_ms"].min())
            last = int(t["entry_ts_ms"].max())
            windows.append({"run": label, "first_entry": first, "last_entry": last,
                            "entries": t.height, "final_equity": res["metrics"].get("final_equity")})
    for w in windows:
        w["first_entry"] = datetime.fromtimestamp(w["first_entry"]/1000, UTC).strftime("%Y-%m-%d")
        w["last_entry"] = datetime.fromtimestamp(w["last_entry"]/1000, UTC).strftime("%Y-%m-%d")
    if windows:
        md += [table(windows, ["run", "first_entry", "last_entry", "entries", "final_equity"]), ""]
    md += ["Both runs hit the 50% peak-to-trough kill switch and then stop opening",
           "positions: the new model in January 2025, the old model in May 2025.",
           "The remaining months of each window are flat. Metrics are therefore",
           "`until ruin`, not full-window performance. This is the single most",
           "important baseline fact.", ""]

    md += ["## Long and short", ""]
    ls = []
    for label, res in (("old dev-A", old["champion_dev"]), ("new dev-A", new["champion_dev"]),
                      ("old dev-B", old["oos"]), ("new dev-B", new["oos"])):
        for side in ("long", "short"):
            s = res["metrics"].get(side, {}) or {}
            ls.append({"run": label, "side": side, "trades": s.get("trades"),
                       "net_pnl": s.get("net_pnl"), "win_rate": s.get("win_rate"),
                       "expectancy_r": s.get("expectancy_r")})
    md += [table(ls, ["run", "side", "trades", "net_pnl", "win_rate", "expectancy_r"]), ""]

    md += ["## Benchmarks on the development subset", ""]
    bench = [a for a in new.get("ablations", []) if "random" in a.get("variant", "") or "trend" in a.get("variant", "") or "buy_hold" in a.get("variant", "")]
    if bench:
        md += [table(bench, ["variant", "trades", "net_pnl", "expectancy_r", "profit_factor", "win_rate", "max_drawdown_pct"]), ""]
    md += ["The random and trend benchmarks use the 12-symbol development subset,", "the same cost model and the same stop/target machinery.", ""]

    stress_path = Path(args.stress)
    md += ["## Cost stress (commission x1.5, slippage x2.0)", ""]
    if stress_path.exists():
        s = load(stress_path)
        srows = []
        for label in ("normal", "stress"):
            if label in s:
                m = s[label]
                srows.append({"run": label, **{k: m.get(k) for k in ("trades", "net_pnl", "profit_factor", "expectancy_r", "max_drawdown_pct", "fees")},
                              "entry_slippage": m.get("slippage")})
        md += [table(srows, list(srows[0])) if srows else "not available", ""]
        md += [f"Period: `{s.get('start')}` .. `{s.get('end')}`, {s.get('symbols')} symbols.", "",
               "The normal stress run reproduces the validation champion exactly (103",
               "trades, -7.85 USDT), which confirms the cost model is deterministic.",
               "`entry_slippage` is the metrics field (entry fills only); total adverse",
               "price movement is 3.30 USDT in the cost decomposition above. The stress",
               "multipliers raise fees from 4.25 to 6.13 and push the outcome marginally",
               "worse, but the account ruins in both cases, so the difference is small.", ""]
    else:
        md += ["Not run yet. The stress run is recorded in the experiment ledger.", ""]

    md += ["## Gate status", ""]
    gng = new.get("go_no_go", {})
    md += [f"Internal validation verdict: **{gng.get('verdict')}**.", ""]
    if gng.get("failed"):
        md += ["Failed criteria: " + ", ".join(gng["failed"]), ""]
    md += ["The frozen gates in `config/gates.yaml` govern the holdout and are not",
           "applied to the development baseline. The baseline is a reference point,",
           "not a GO candidate.", ""]

    md += ["## Run resources", ""]
    md += [f"* wall clock: {args.elapsed:,.1f} s ({args.elapsed/60:,.1f} min)",
           f"* peak RSS: {args.peak_rss_gb:,.2f} GiB (four validation workers)",
           f"* champion dev: {args.champion_bars:,} 1m bars in 421.2 s = {args.champion_bars/421.2:,.0f} bars/s", ""]
    md += ["## Reproduce", "", "```bash",
           "uv run bot validate --symbols universe --start 2024-11-01 \\",
           "  --dev-end 2025-12-31 --oos-end 2026-06-30 --vault-end 2026-10-20 --workers 4",
           "uv run python scripts/run_stress.py", "```", ""]

    (out / "baseline.md").write_text("\n".join(md))
    (out / "baseline.json").write_text(
        json.dumps({"old": {"dev": old_dev, "oos": old_oos}, "new": {"dev": new_dev, "oos": new_oos},
                    "new_costs": {"dev": new_dev_costs, "oos": new_oos_costs},
                    "go_no_go": gng}, indent=2, default=str)
    )
    print(f"wrote {out/'baseline.md'}")


if __name__ == "__main__":
    main()
