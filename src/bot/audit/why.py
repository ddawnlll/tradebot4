"""Why does the system lose? A reproducible loss-accounting audit.

The audit is intentionally mechanical: it decomposes a run into gross edge,
slippage, fees, funding and net, ranks exit channels, sides, symbols and
months, and states the arithmetic levers. It does not propose a new strategy.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import polars as pl

COLUMNS: dict[str, pl.DataType] = {
    "ts_utc": pl.Utf8,
    "source": pl.Utf8,
    "run_id": pl.Utf8,
    "strategy": pl.Utf8,
    "period_start": pl.Utf8,
    "period_end": pl.Utf8,
    "trades": pl.Int64,
    "gross_before_slippage": pl.Float64,
    "slippage": pl.Float64,
    "fees": pl.Float64,
    "funding": pl.Float64,
    "net_pnl": pl.Float64,
    "profit_factor": pl.Float64,
    "expectancy_r": pl.Float64,
    "win_rate": pl.Float64,
    "max_drawdown_pct": pl.Float64,
    "last_entry": pl.Utf8,
    "kill_switch": pl.Boolean,
    "top_loss_reason": pl.Utf8,
    "top_loss_usdt": pl.Float64,
    "top_loss_share": pl.Float64,
    "long_net": pl.Float64,
    "short_net": pl.Float64,
    "cost_per_trade": pl.Float64,
    "gross_per_trade": pl.Float64,
    "breakeven_gross_per_trade": pl.Float64,
    "random_expectancy": pl.Float64,
    "baseline_4f_expectancy": pl.Float64,
    "verdict": pl.Utf8,
    "notes": pl.Utf8,
}


def _read(run_dir: str | Path, name: str) -> pl.DataFrame:
    path = Path(run_dir) / name
    return pl.read_parquet(path) if path.exists() else pl.DataFrame()


def _iso(ts_ms: int | None) -> str | None:
    if ts_ms is None:
        return None
    return datetime.fromtimestamp(int(ts_ms) / 1000, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _slippage(fills: pl.DataFrame) -> float:
    if fills.height == 0 or "reference_price" not in fills.columns:
        return 0.0
    f = fills.filter(pl.col("reference_price").is_not_null() & (pl.col("reference_price") > 0))
    if f.height == 0:
        return 0.0
    f = f.with_columns(
        pl.when(pl.col("side").is_in(["BUY", "Buy", "buy"])).then(1.0).otherwise(-1.0).alias("sign"),
        (pl.col("price") - pl.col("reference_price")).alias("diff"),
    )
    return float((f["diff"] * f["sign"] * f["qty"]).sum())


def audit_run(run_dir: str | Path, starting_balance: float = 20.0, metric: dict | None = None) -> dict[str, Any]:
    run_dir = Path(run_dir)
    trades = _read(run_dir, "trades.parquet")
    fills = _read(run_dir, "fills.parquet")
    if metric is None:
        mp = run_dir / "metrics.json"
        metric = json.loads(mp.read_text()) if mp.exists() else {}
    if trades.height == 0:
        return {"run_dir": str(run_dir), "trades": 0, "net_pnl": 0.0, "cost": {}, "exits": [], "sides": [],
                "months": [], "symbols": [], "mae_mfe": {}, "risk": {}, "root_causes": [], "levers": [],
                "metrics": metric}

    n = trades.height
    gross = float(trades["gross_pnl"].sum())
    fees = float(trades["fees"].sum())
    funding = float(trades["funding"].sum())
    net = float(trades["net_pnl"].sum())
    slip = _slippage(fills)
    gross_before_slip = gross + slip
    total_cost = fees + slip - funding
    cost_per_trade = total_cost / n
    gross_per_trade = gross / n
    breakeven = cost_per_trade

    exits = (
        trades.group_by("exit_reason")
        .agg(
            pl.len().alias("trades"),
            pl.col("net_pnl").sum().alias("net_pnl"),
            pl.col("net_r").mean().alias("expectancy_r"),
            (pl.col("net_r") > 0).mean().alias("win_rate"),
            pl.col("mae_r").mean().alias("mae_r"),
            pl.col("mfe_r").mean().alias("mfe_r"),
        )
        .sort("net_pnl")
    )
    negative_total = float(exits.filter(pl.col("net_pnl") < 0)["net_pnl"].sum())
    exits = exits.with_columns(
        (pl.col("net_pnl") / negative_total if negative_total < 0 else pl.lit(0.0)).alias("loss_share")
    )

    sides = (
        trades.group_by("side")
        .agg(pl.len().alias("trades"), pl.col("net_pnl").sum().alias("net_pnl"),
             pl.col("net_r").mean().alias("expectancy_r"), (pl.col("net_r") > 0).mean().alias("win_rate"))
        .sort("net_pnl")
    )
    months = (
        trades.with_columns(pl.from_epoch("entry_ts_ms", "ms").dt.strftime("%Y-%m").alias("month"))
        .group_by("month")
        .agg(pl.len().alias("trades"), pl.col("gross_pnl").sum().alias("gross_pnl"),
             pl.col("net_pnl").sum().alias("net_pnl"))
        .sort("month")
    )
    by_symbol = (
        trades.group_by("symbol")
        .agg(pl.len().alias("trades"), pl.col("net_pnl").sum().alias("net_pnl"),
             pl.col("net_r").mean().alias("expectancy_r"))
        .sort("net_pnl")
    )
    losers = by_symbol.head(5)
    winners = by_symbol.tail(5).sort("net_pnl", descending=True)
    top5_loss_share = (
        float(losers.filter(pl.col("net_pnl") < 0)["net_pnl"].sum() / negative_total)
        if negative_total < 0 else 0.0
    )

    mae = trades["mae_r"].drop_nulls()
    mfe = trades["mfe_r"].drop_nulls()
    mae_mfe = {
        "mae_mean": float(mae.mean()) if mae.len() else None,
        "mae_median": float(mae.median()) if mae.len() else None,
        "mfe_mean": float(mfe.mean()) if mfe.len() else None,
        "mfe_median": float(mfe.median()) if mfe.len() else None,
        "mae_le_1r": int((mae <= -1).sum()) if mae.len() else 0,
        "mfe_ge_1r": int((mfe >= 1).sum()) if mfe.len() else 0,
        "realized_le_1r": int((trades["net_r"] <= -1).sum()),
        "realized_le_0_5r": int((trades["net_r"] <= -0.5).sum()),
    }

    first_entry = int(trades["entry_ts_ms"].min())
    last_entry = int(trades["entry_ts_ms"].max())
    max_dd = float(metric.get("max_drawdown_pct") or 0.0)
    risk = {
        "first_entry": _iso(first_entry),
        "last_entry": _iso(last_entry),
        "final_equity": metric.get("final_equity"),
        "max_drawdown_pct": max_dd,
        "kill_switch": max_dd >= 50.0,
        "avg_bars_held": float(trades["bars_held"].mean()) if "bars_held" in trades.columns else None,
    }

    top = exits.row(0, named=True) if exits.height else {}
    top_reason = top.get("exit_reason")
    top_net = float(top.get("net_pnl") or 0.0)
    top_share = float(top.get("loss_share") or 0.0)
    long_net = float(sides.filter(pl.col("side") == "LONG")["net_pnl"].sum()) if sides.height else 0.0
    short_net = float(sides.filter(pl.col("side") == "SHORT")["net_pnl"].sum()) if sides.height else 0.0
    pf = metric.get("profit_factor")
    exp = metric.get("expectancy_r")
    win = metric.get("win_rate")

    gross_before_per_trade = gross_before_slip / n
    causes: list[dict[str, Any]] = []
    if gross_before_per_trade <= 0:
        causes.append({
            "severity": "high", "cause": "No gross edge before costs",
            "evidence": f"gross {gross_before_per_trade:+.4f} USDT/trade before slippage and fees; the old optimistic model's +0.0116 was not robust.",
        })
    if cost_per_trade > 0 and gross_before_per_trade <= cost_per_trade:
        mult = (cost_per_trade / gross_before_per_trade) if gross_before_per_trade > 0 else None
        causes.append({
            "severity": "high", "cause": "Cost load exceeds the gross edge",
            "evidence": f"cost {cost_per_trade:.4f} USDT/trade vs gross before costs {gross_before_per_trade:+.4f}; "
                        + (f"break-even needs {mult:.1f}x the gross edge." if mult else "gross is negative, no finite multiple helps."),
        })
    if top_reason in ("BREAKOUT", "STOP") and top_share >= 0.5:
        causes.append({
            "severity": "high", "cause": f"Exit realization channel: {top_reason}",
            "evidence": f"{top_reason} is {top_share:.1%} of all negative trade PnL ({top_net:+.2f} USDT); "
                        "the loss is realized by the exit rule, not only by entry selection.",
        })
    if long_net < 0 and short_net > long_net:
        causes.append({
            "severity": "medium", "cause": "Long side carries the loss",
            "evidence": f"long {long_net:+.2f} USDT vs short {short_net:+.2f} USDT.",
        })
    if max_dd >= 50.0:
        causes.append({
            "severity": "high", "cause": "Ruin before sample maturity",
            "evidence": f"max drawdown {max_dd:.1f}% hits the 50% kill switch; entries stop at {_iso(last_entry)}.",
        })

    levers = [
        {"lever": "zero fees (arithmetic)", "net": net + fees, "note": "not achievable at VIP0; shows fee share."},
        {"lever": "zero slippage (arithmetic)", "net": net + slip, "note": "not achievable; shows execution share."},
        {"lever": "zero all costs (arithmetic)", "net": net + total_cost, "note": "upper bound before any strategy change."},
    ]
    if top_reason and top_net < 0:
        levers.append({
            "lever": f"halve {top_reason} losses (in-sample arithmetic)",
            "net": net + 0.5 * abs(top_net),
            "note": "counterfactual is not simulated; a tested exit variant is required.",
        })

    audit = {
        "run_dir": str(run_dir),
        "trades": n,
        "gross": gross,
        "slippage": slip,
        "fees": fees,
        "funding": funding,
        "net": net,
        "gross_before_slippage": gross_before_slip,
        "total_cost": total_cost,
        "cost_per_trade": cost_per_trade,
        "gross_per_trade": gross_per_trade,
        "gross_before_per_trade": gross_before_per_trade,
        "breakeven_gross_per_trade": breakeven,
        "cost_pct_equity_per_trade": 100.0 * cost_per_trade / starting_balance,
        "pf": pf,
        "expectancy_r": exp,
        "win_rate": win,
        "residual": net - (gross - fees + funding),
        "exits": exits.to_dicts(),
        "sides": sides.to_dicts(),
        "months": months.to_dicts(),
        "top_losers": losers.to_dicts(),
        "top_winners": winners.to_dicts(),
        "top5_loss_share": top5_loss_share,
        "mae_mfe": mae_mfe,
        "risk": risk,
        "top_loss_reason": top_reason,
        "top_loss_usdt": top_net,
        "top_loss_share": top_share,
        "long_net": long_net,
        "short_net": short_net,
        "root_causes": causes,
        "levers": levers,
        "metrics": metric,
    }
    return audit


def _f(value: Any, nd: int = 3) -> str:
    if value is None:
        return "-"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, int):
        return str(value)
    try:
        return f"{float(value):+,.{nd}f}"
    except Exception:
        return str(value)


def _u(value: Any, nd: int = 3) -> str:
    if value is None:
        return "-"
    try:
        return f"{float(value):,.{nd}f}"
    except Exception:
        return str(value)


def _table(rows: list[dict], cols: list[str]) -> str:
    head = "| " + " | ".join(cols) + " |"
    sep = "|" + "|".join("---" for _ in cols) + "|"
    body = ["| " + " | ".join(str(r.get(c, "-")) for c in cols) + " |" for r in rows]
    return "\n".join([head, sep, *body])


def render_markdown(audit: dict, history: pl.DataFrame | None = None, title: str = "Why the system does not profit", verdict: str = "NO_GO") -> str:
    if not audit.get("trades"):
        return f"# {title}\n\nNo trades; nothing to audit.\n"
    costs = [
        {"line": "gross before slippage", "usdt": _f(audit["gross_before_slippage"], 3), "per_trade": _f(audit["gross_before_slippage"] / audit["trades"], 4)},
        {"line": "slippage", "usdt": _f(-audit["slippage"], 3), "per_trade": _f(-audit["slippage"] / audit["trades"], 4)},
        {"line": "fees", "usdt": _f(-audit["fees"], 3), "per_trade": _f(-audit["fees"] / audit["trades"], 4)},
        {"line": "funding", "usdt": _f(audit["funding"], 3), "per_trade": _f(audit["funding"] / audit["trades"], 4)},
        {"line": "net", "usdt": _f(audit["net"], 3), "per_trade": _f(audit["net"] / audit["trades"], 4)},
    ]
    exits = [{"reason": e["exit_reason"], "trades": e["trades"], "net": _f(e["net_pnl"], 2),
              "exp R": _f(e["expectancy_r"], 3), "win": _u(e["win_rate"], 3),
              "loss share": _f(e["loss_share"], 3), "MAE": _f(e["mae_r"], 2), "MFE": _f(e["mfe_r"], 2)}
             for e in audit["exits"]]
    sides = [{"side": s["side"], "trades": s["trades"], "net": _f(s["net_pnl"], 2),
              "exp R": _f(s["expectancy_r"], 3), "win": _u(s["win_rate"], 3)} for s in audit["sides"]]
    months = [{"month": m["month"], "trades": m["trades"], "gross": _f(m["gross_pnl"], 2), "net": _f(m["net_pnl"], 2)}
              for m in audit["months"]]
    causes = [{"severity": c["severity"], "cause": c["cause"], "evidence": c["evidence"]} for c in audit["root_causes"]]
    levers = [{"lever": x["lever"], "net": _f(x["net"], 2), "note": x["note"]} for x in audit["levers"]]
    risk = audit["risk"]
    md = [
        f"# {title}",
        "",
        "Generated by `scripts/audit_why_no_edge.py` / `bot.audit.why`. This is an accounting audit, not a counterfactual simulation.",
        "",
        f"* run: `{audit['run_dir']}`",
        f"* trades: {audit['trades']}, period: {risk.get('first_entry')} .. {risk.get('last_entry')}",
        f"* net: {_f(audit['net'], 2)} USDT, PF: {_u(audit['pf'], 3)}, expectancy: {_f(audit['expectancy_r'], 4)}R, win rate: {_u(audit['win_rate'], 3)}",
        f"* max drawdown: {_u(risk.get('max_drawdown_pct'), 1)}%, kill switch: {_f(risk.get('kill_switch'), 0)}, final equity: {_u(risk.get('final_equity'), 2)}",
        "",
        f"**Verdict: {verdict}.** Gross before costs {audit['gross_before_per_trade']:+.4f} USDT/trade, "
        f"total cost {audit['cost_per_trade']:.4f}/trade, {audit['top_loss_reason']} is "
        f"{audit['top_loss_share']:.1%} of negative PnL, long {audit['long_net']:+.2f} vs short "
        f"{audit['short_net']:+.2f}, max drawdown {risk.get('max_drawdown_pct', 0):.1f}% stops entries at {risk.get('last_entry')}.",
        "",
        "## Cost waterfall",
        "",
        _table(costs, ["line", "usdt", "per_trade"]),
        "",
        f"The round trip costs {audit['cost_pct_equity_per_trade']:.2f}% of starting equity per trade. "
        f"Break-even gross before costs is {audit['breakeven_gross_per_trade']:.4f} USDT per trade; "
        f"the run's gross before costs is {audit['gross_before_per_trade']:+.4f} USDT per trade.",
        "",
        "## Exit channels",
        "",
        _table(exits, ["reason", "trades", "net", "exp R", "win", "loss share", "MAE", "MFE"]),
        "",
        "`loss share` is the share of all negative trade PnL. MAE/MFE are averages in R.",
        "",
        "## Sides",
        "",
        _table(sides, ["side", "trades", "net", "exp R", "win"]),
        "",
        "## Months",
        "",
        _table(months, ["month", "trades", "gross", "net"]),
        "",
        f"Top 5 losing symbols account for {audit['top5_loss_share']:.1%} of negative PnL. "
        f"MAE mean/median {_f(audit['mae_mfe'].get('mae_mean'), 2)}/{_f(audit['mae_mfe'].get('mae_median'), 2)}R; "
        f"MFE mean/median {_f(audit['mae_mfe'].get('mfe_mean'), 2)}/{_f(audit['mae_mfe'].get('mfe_median'), 2)}R. "
        f"{audit['mae_mfe'].get('mae_le_1r', 0)} trades had MAE <= -1R, {audit['mae_mfe'].get('mfe_ge_1r', 0)} had MFE >= +1R; "
        f"{audit['mae_mfe'].get('realized_le_1r', 0)} realized <= -1R and {audit['mae_mfe'].get('realized_le_0_5r', 0)} realized <= -0.5R.",
        "",
        "## Root causes (ranked)",
        "",
        _table(causes, ["severity", "cause", "evidence"]),
        "",
        "## Arithmetic levers (not forecasts)",
        "",
        _table(levers, ["lever", "net", "note"]),
        "",
        "These are net PnL plus the named cost or loss component. A real fix must be a preregistered strategy change and a new walk-forward run.",
        "",
    ]
    if history is not None and history.height:
        hrows = []
        for r in history.tail(10).iter_rows(named=True):
            hrows.append({"date": str(r["ts_utc"])[:10], "source": r["source"], "trades": r["trades"],
                          "net": _f(r["net_pnl"], 2), "pf": _u(r["profit_factor"], 3),
                          "exp": _f(r["expectancy_r"], 4), "top loss": r["top_loss_reason"],
                          "verdict": r["verdict"]})
        md += ["## Audit history", "", _table(hrows, ["date", "source", "trades", "net", "pf", "exp", "top loss", "verdict"]), ""]
    md += ["## Reproduce", "", "```bash", "uv run python scripts/audit_why_no_edge.py", "```", ""]
    return "\n".join(md)


def append_history(audit: dict, path: str | Path, source: str, verdict: str, notes: str = "") -> pl.DataFrame:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    row = {
        "ts_utc": datetime.now(UTC).isoformat(),
        "source": source,
        "run_id": Path(audit["run_dir"]).name,
        "strategy": audit.get("strategy") or audit["metrics"].get("strategy") or "",
        "period_start": audit["risk"].get("first_entry"),
        "period_end": audit["risk"].get("last_entry"),
        "trades": audit["trades"],
        "gross_before_slippage": audit["gross_before_slippage"],
        "slippage": audit["slippage"],
        "fees": audit["fees"],
        "funding": audit["funding"],
        "net_pnl": audit["net"],
        "profit_factor": audit["pf"],
        "expectancy_r": audit["expectancy_r"],
        "win_rate": audit["win_rate"],
        "max_drawdown_pct": audit["risk"].get("max_drawdown_pct"),
        "last_entry": audit["risk"].get("last_entry"),
        "kill_switch": audit["risk"].get("kill_switch"),
        "top_loss_reason": audit["top_loss_reason"],
        "top_loss_usdt": audit["top_loss_usdt"],
        "top_loss_share": audit["top_loss_share"],
        "long_net": audit["long_net"],
        "short_net": audit["short_net"],
        "cost_per_trade": audit["cost_per_trade"],
        "gross_per_trade": audit["gross_per_trade"],
        "breakeven_gross_per_trade": audit["breakeven_gross_per_trade"],
        "random_expectancy": audit.get("random_expectancy"),
        "baseline_4f_expectancy": audit.get("baseline_4f_expectancy"),
        "verdict": verdict,
        "notes": notes,
    }
    new = pl.DataFrame([row], schema=COLUMNS)
    if path.exists():
        old = pl.read_parquet(path)
        if old.height and old.filter(pl.col("run_id") == row["run_id"]).height:
            return old
        df = pl.concat([old, new], how="vertical_relaxed")
    else:
        df = new
    df.write_parquet(path, compression="zstd", statistics=True)
    return df


def write_audit_report(
    validation: dict,
    *,
    root: Path | None = None,
    doc_path: str | Path | None = None,
    history_path: str | Path | None = None,
    source: str | None = None,
) -> dict:
    root = root or Path(__file__).resolve().parents[3]
    doc_path = Path(doc_path) if doc_path else root / "docs" / "audits" / "why_no_edge.md"
    history_path = Path(history_path) if history_path else root / "kaizen" / "audit_history.parquet"
    champion = validation.get("champion_dev", {})
    run_dir = champion.get("run_dir")
    if not run_dir:
        raise ValueError("validation report has no champion_dev.run_dir")
    base = validation.get("base_config", {})
    audit = audit_run(run_dir, starting_balance=float(base.get("starting_balance", 20.0)), metric=champion.get("metrics"))
    audit["strategy"] = base.get("strategy", "")
    summary_path = root / "reports" / "research_round1" / "summary.json"
    gates_path = root / "reports" / "research_round1" / "gates.json"
    if summary_path.exists():
        summary = json.loads(summary_path.read_text())
        audit["random_expectancy"] = (summary.get("random") or {}).get("expectancy_r")
        audit["baseline_4f_expectancy"] = (summary.get("baseline") or {}).get("expectancy_r")
        audit["hypotheses"] = []
        gates = json.loads(gates_path.read_text()) if gates_path.exists() else {"hypotheses": {}}
        for name, block in summary.get("hypotheses", {}).items():
            best = block.get("best")
            agg = block.get("configs", {}).get(best, {})
            gate = gates.get("hypotheses", {}).get(name, {}).get("gates", {}).get(best, {})
            audit["hypotheses"].append({"name": name, "best": best, "trades": agg.get("trades"),
                                        "pf": agg.get("profit_factor"), "exp": agg.get("expectancy_r"),
                                        "stress_pf": (block.get("stress") or {}).get("profit_factor"),
                                        "verdict": gate.get("configuration_verdict", False)})
    verdict = (validation.get("go_no_go") or {}).get("verdict", "NO_GO")
    src = source or f"validation_{validation.get('generated', '')[:10]}"
    history = append_history(audit, history_path, src, verdict)
    md = render_markdown(audit, history, verdict=verdict)
    if audit.get("hypotheses"):
        rows = [{"hypothesis": h["name"], "best": h["best"], "trades": h["trades"],
                 "PF": _u(h["pf"], 3), "exp R": _f(h["exp"], 4),
                 "stress PF": _u(h["stress_pf"], 3), "verdict": "PASS" if h["verdict"] else "FAIL"} for h in audit["hypotheses"]]
        section = "## Hypothesis results\n\n" + _table(rows, ["hypothesis", "best", "trades", "PF", "exp R", "stress PF", "verdict"]) + "\n\n"
        md = md.replace("## Root causes (ranked)", section + "## Root causes (ranked)")
    doc_path.parent.mkdir(parents=True, exist_ok=True)
    doc_path.write_text(md)
    return audit
