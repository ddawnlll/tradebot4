"""Why does the system lose? A reproducible loss-accounting audit.

The audit is intentionally mechanical: it decomposes a run into gross edge,
slippage, fees, funding and net, ranks exit channels, sides, symbols and
months, and states the arithmetic levers. It does not propose a new strategy.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import polars as pl

try:  # POSIX only; the fallback below keeps the writer usable elsewhere.
    import fcntl
except ImportError:  # pragma: no cover - exercised only on non-POSIX platforms
    fcntl = None  # type: ignore[assignment]

COLUMNS: dict[str, pl.DataType] = {
    "ts_utc": pl.Utf8,
    "source": pl.Utf8,
    "validation_source": pl.Utf8,
    "run_id": pl.Utf8,
    "config_hash": pl.Utf8,
    "periods": pl.Utf8,
    "identity_key": pl.Utf8,
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
    ]
    context = audit.get("research_context")
    if context:
        if context.get("attached"):
            md += ["## Research context", "",
                   f"Hypothesis and benchmark context attached from `{context.get('summary')}` "
                   "after its recorded run_id/period identity matched this validation.", ""]
        else:
            md += ["## Research context", "",
                   f"Not attached: {context.get('reason')}. Hypothesis, random and baseline values are "
                   "omitted rather than mixing results from another run, config or period.", ""]
    md += [
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


def _stable_identity(run_id: str, period_start: str | None, period_end: str | None,
                     config_hash: str | None) -> str:
    raw = "|".join([run_id or "", period_start or "", period_end or "", config_hash or ""])
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def _align_history(df: pl.DataFrame) -> pl.DataFrame:
    missing = [name for name in COLUMNS if name not in df.columns]
    if missing:
        df = df.with_columns([pl.lit(None, dtype=COLUMNS[name]).alias(name) for name in missing])
    ordered = [name for name in COLUMNS if name in df.columns]
    extras = [name for name in df.columns if name not in COLUMNS]
    return df.select(ordered + extras)


@contextmanager
def _history_lock(path: Path) -> Iterator[None]:
    """Serialise the read-modify-write cycle for one history file.

    ``fcntl.flock`` is released automatically by the kernel if the process
    dies. The directory fallback exists for platforms without ``flock``.
    """
    lock_path = path.with_name(path.name + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fallback_dir = path.with_name(path.name + ".lockdir")
    fallback_held = False
    with open(lock_path, "a+") as handle:
        try:
            if fcntl is not None:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            else:  # pragma: no cover - exercised only on non-POSIX platforms
                deadline = time.monotonic() + 60.0
                while True:
                    try:
                        os.mkdir(fallback_dir)
                        fallback_held = True
                        break
                    except FileExistsError:
                        if time.monotonic() > deadline:
                            raise TimeoutError(f"timed out waiting for history lock {fallback_dir}") from None
                        time.sleep(0.01)
            yield
        finally:
            if fcntl is not None:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            elif fallback_held:  # pragma: no cover - exercised only on non-POSIX platforms
                os.rmdir(fallback_dir)


def _atomic_write_parquet(df: pl.DataFrame, path: Path) -> None:
    """Write a complete parquet file, then atomically swap it into place."""
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    try:
        df.write_parquet(tmp, compression="zstd", statistics=True)
        with open(tmp, "rb") as handle:
            os.fsync(handle.fileno())
        os.replace(tmp, path)
        try:
            dir_fd = os.open(path.parent, os.O_RDONLY)
        except OSError:  # pragma: no cover - directory fsync is best effort
            return
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    finally:
        if tmp.exists():
            tmp.unlink()


def append_history(
    audit: dict,
    path: str | Path,
    source: str,
    verdict: str,
    notes: str = "",
    *,
    validation_source: str | None = None,
    config_hash: str | None = None,
) -> pl.DataFrame:
    """Append one audit row to the history table.

    Parquet has no physical append: the whole table is read, the new row is
    concatenated, and the table is rewritten. The rewrite is protected by a
    process lock and completed with ``os.replace`` so concurrent appends
    cannot lose rows and readers never observe a partial file.

    Idempotency uses a stable identity, not the display ``source`` label:
    ``run_id`` plus the audited period plus ``config_hash`` (the
    config/validation identity). Re-appending the same run, even under a
    different ``source`` label, is a no-op.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    run_id = str(audit.get("run_id") or Path(audit["run_dir"]).name)
    risk = audit["risk"]
    period_start = risk.get("first_entry")
    period_end = risk.get("last_entry")
    cfg_hash = config_hash if config_hash is not None else audit.get("config_hash")
    v_source = validation_source or audit.get("validation_source") or source
    identity = _stable_identity(run_id, period_start, period_end, cfg_hash)
    periods = audit.get("periods")
    row = {
        "ts_utc": datetime.now(UTC).isoformat(),
        "source": source,
        "validation_source": v_source,
        "run_id": run_id,
        "config_hash": cfg_hash,
        "periods": json.dumps(periods, sort_keys=True) if periods else None,
        "identity_key": identity,
        "strategy": audit.get("strategy") or audit["metrics"].get("strategy") or "",
        "period_start": period_start,
        "period_end": period_end,
        "trades": audit["trades"],
        "gross_before_slippage": audit["gross_before_slippage"],
        "slippage": audit["slippage"],
        "fees": audit["fees"],
        "funding": audit["funding"],
        "net_pnl": audit["net"],
        "profit_factor": audit["pf"],
        "expectancy_r": audit["expectancy_r"],
        "win_rate": audit["win_rate"],
        "max_drawdown_pct": risk.get("max_drawdown_pct"),
        "last_entry": risk.get("last_entry"),
        "kill_switch": risk.get("kill_switch"),
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
    with _history_lock(path):
        raw_old = pl.read_parquet(path) if path.exists() else None
        old_columns = set(raw_old.columns) if raw_old is not None else set()
        if raw_old is not None and raw_old.height:
            if "identity_key" in old_columns:
                duplicate = raw_old.filter(
                    (pl.col("identity_key") == identity)
                    | (pl.col("identity_key").is_null() & (pl.col("run_id") == run_id))
                ).height
            else:
                # Legacy rows predate the identity column. Preserve the old
                # run_id-based no-op instead of duplicating them on upgrade.
                duplicate = raw_old.filter(pl.col("run_id") == run_id).height
            if duplicate:
                aligned = _align_history(raw_old)
                legacy = aligned["identity_key"].is_null() & (aligned["run_id"] == run_id)
                aligned = aligned.with_columns(
                    pl.when(legacy).then(pl.lit(identity)).otherwise(pl.col("identity_key")).alias("identity_key"),
                    pl.when(legacy).then(pl.lit(v_source)).otherwise(pl.col("validation_source")).alias("validation_source"),
                    pl.when(legacy).then(pl.lit(cfg_hash)).otherwise(pl.col("config_hash")).alias("config_hash"),
                    pl.when(legacy).then(pl.lit(json.dumps(periods, sort_keys=True) if periods else None)).otherwise(pl.col("periods")).alias("periods"),
                )
                _atomic_write_parquet(aligned, path)
                return aligned
        if raw_old is not None:
            raw_old = _align_history(raw_old)
        df = pl.concat([raw_old, new], how="vertical_relaxed") if raw_old is not None else new
        df = _align_history(df)
        _atomic_write_parquet(df, path)
        return df


def _config_hash(base: dict) -> str | None:
    if not base:
        return None
    payload = json.dumps(base, sort_keys=True, default=str).encode()
    return hashlib.sha256(payload).hexdigest()[:16]


def _int_pair(value: Any) -> list[int] | None:
    if isinstance(value, (list, tuple)) and len(value) == 2:
        try:
            return [int(value[0]), int(value[1])]
        except (TypeError, ValueError):
            return None
    return None


def _periods_match(identity: dict, periods: dict | None) -> bool:
    if not periods:
        return False
    identity_periods = identity.get("periods")
    if isinstance(identity_periods, dict) and identity_periods:
        compared = False
        for key in ("dev", "oos", "vault"):
            if key in identity_periods and key in periods:
                compared = True
                if _int_pair(identity_periods[key]) != _int_pair(periods[key]):
                    return False
        return compared
    start, end = identity.get("period_start"), identity.get("period_end")
    if start is not None and end is not None:
        return _int_pair([start, end]) == _int_pair(periods.get("dev"))
    return False


def _research_identity(summary: dict) -> dict | None:
    identity = summary.get("identity") or summary.get("research_identity")
    if identity is None and isinstance(summary.get("champion_dev"), dict):
        identity = summary["champion_dev"]
    return identity if isinstance(identity, dict) else None


def _load_research_context(
    *,
    root: Path,
    validation: dict,
    champion_run_id: str,
    champion_config_hash: str | None,
    research_summary: str | Path | None,
) -> tuple[dict | None, str, Path | None]:
    """Attach research context only when its recorded identity matches this run.

    A summary without a recorded identity (``identity`` or
    ``research_identity`` with ``run_id`` and ``periods``) is never attached,
    because there is no way to prove that its hypotheses belong to this
    validation run, config and period.
    """
    if research_summary:
        candidates = [Path(research_summary)]
    elif validation.get("research_summary"):
        candidates = [Path(validation["research_summary"])]
    else:
        candidates = sorted(
            root.glob("reports/research_*/summary.json"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
    if not candidates:
        return None, "no research summary found; hypothesis context omitted", None

    periods = validation.get("periods")
    first_reason = ""
    for summary_path in candidates:
        if not summary_path.exists():
            first_reason = first_reason or f"research summary {summary_path} does not exist"
            continue
        try:
            summary = json.loads(summary_path.read_text())
        except (OSError, json.JSONDecodeError) as exc:
            first_reason = first_reason or f"research summary {summary_path} is unreadable: {exc}"
            continue
        identity = _research_identity(summary)
        if identity is None:
            first_reason = first_reason or (
                f"research summary {summary_path} has no recorded identity (run_id/periods); "
                "refusing to attach it"
            )
            continue
        if identity.get("run_id") != champion_run_id:
            first_reason = first_reason or (
                f"research summary {summary_path} belongs to run_id {identity.get('run_id')!r}, "
                f"not {champion_run_id!r}; context omitted"
            )
            continue
        if not _periods_match(identity, periods):
            first_reason = first_reason or (
                f"research summary {summary_path} periods do not match this validation; context omitted"
            )
            continue
        recorded_config = identity.get("config_hash")
        if recorded_config and champion_config_hash and recorded_config != champion_config_hash:
            first_reason = first_reason or (
                f"research summary {summary_path} config_hash {recorded_config!r} does not match "
                f"{champion_config_hash!r}; context omitted"
            )
            continue
        gates_path = summary_path.with_name("gates.json")
        gates = json.loads(gates_path.read_text()) if gates_path.exists() else {"hypotheses": {}}
        return {
            "summary": summary,
            "gates": gates,
            "summary_path": summary_path,
            "identity": identity,
        }, "", summary_path
    return None, first_reason or "research summary identity did not match; context omitted", candidates[0]


def write_audit_report(
    validation: dict,
    *,
    root: Path | None = None,
    doc_path: str | Path | None = None,
    history_path: str | Path | None = None,
    source: str | None = None,
    research_summary: str | Path | None = None,
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
    run_id = str(champion.get("run_id") or Path(run_dir).name)
    src = source or f"validation_{validation.get('generated', '')[:10]}"
    cfg_hash = champion.get("config_hash") or _config_hash(base)
    audit["run_id"] = run_id
    audit["validation_source"] = src
    audit["config_hash"] = cfg_hash
    audit["periods"] = validation.get("periods")

    context, reason, matched_path = _load_research_context(
        root=root,
        validation=validation,
        champion_run_id=run_id,
        champion_config_hash=cfg_hash,
        research_summary=research_summary,
    )
    if context is not None:
        summary = context["summary"]
        gates = context["gates"]
        audit["random_expectancy"] = (summary.get("random") or {}).get("expectancy_r")
        audit["baseline_4f_expectancy"] = (summary.get("baseline") or {}).get("expectancy_r")
        audit["hypotheses"] = []
        for name, block in summary.get("hypotheses", {}).items():
            best = block.get("best")
            agg = block.get("configs", {}).get(best, {})
            gate = gates.get("hypotheses", {}).get(name, {}).get("gates", {}).get(best, {})
            audit["hypotheses"].append({"name": name, "best": best, "trades": agg.get("trades"),
                                        "pf": agg.get("profit_factor"), "exp": agg.get("expectancy_r"),
                                        "stress_pf": (block.get("stress") or {}).get("profit_factor"),
                                        "verdict": gate.get("configuration_verdict", False)})
        audit["research_context"] = {
            "attached": True,
            "summary": str(context["summary_path"]),
            "identity": context["identity"],
        }
        context_note = ""
    else:
        # Never attach another run's hypotheses or benchmark values.
        audit["random_expectancy"] = None
        audit["baseline_4f_expectancy"] = None
        audit["hypotheses"] = None
        audit["research_context"] = {
            "attached": False,
            "reason": reason,
            "summary": str(matched_path) if matched_path else None,
            "expected": {"run_id": run_id, "periods": validation.get("periods"), "config_hash": cfg_hash},
        }
        context_note = f"research context not attached: {reason}"
    verdict = (validation.get("go_no_go") or {}).get("verdict", "NO_GO")
    history = append_history(
        audit,
        history_path,
        src,
        verdict,
        notes=context_note,
        validation_source=src,
        config_hash=cfg_hash,
    )
    audit["history_path"] = str(history_path)
    audit["history_rows"] = history.height
    md = render_markdown(audit, history, verdict=verdict)
    if audit.get("hypotheses"):
        rows = [{"hypothesis": h["name"], "best": h["best"], "trades": h["trades"],
                 "PF": _u(h["pf"], 3), "exp R": _f(h["exp"], 4),
                 "stress PF": _u(h["stress_pf"], 3), "verdict": "PASS" if h["verdict"] else "FAIL"} for h in audit["hypotheses"]]
        section = "## Hypothesis results\n\n" + _table(rows, ["hypothesis", "best", "trades", "PF", "exp R", "stress PF", "verdict"]) + "\n\n"
        md = md.replace("## Root causes (ranked)", section + "## Root causes (ranked)")
    doc_path.parent.mkdir(parents=True, exist_ok=True)
    doc_path.write_text(md)
    audit["audit_doc"] = str(doc_path)
    return audit
