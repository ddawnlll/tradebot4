"""Walk-forward evaluation of the cost hypotheses on development data only."""

from __future__ import annotations

import argparse
import json
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import polars as pl

from bot.backtest.runner import BacktestConfig, run_backtest
from bot.backtest.statistics import bootstrap_mean_ci
from bot.core.market.scoring import RangeScoreConfig
from bot.data.storage import Paths
from bot.kaizen.experiments import record_experiment

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "reports" / "research_round1"


def _ms(year: int, month: int, day: int = 1) -> int:
    return int(datetime(year, month, day, tzinfo=UTC).timestamp() * 1000)


FOLDS = [
    ("2024-11-01", "2025-03-31", _ms(2024, 11), _ms(2025, 4)),
    ("2025-04-01", "2025-08-31", _ms(2025, 4), _ms(2025, 9)),
    ("2025-09-01", "2026-01-31", _ms(2025, 9), _ms(2026, 2)),
    ("2026-02-01", "2026-06-30", _ms(2026, 2), _ms(2026, 7)),
]

BASE = {
    "id": "baseline",
    "strategy": "range_v1",
    "params": {},
    "range_score": None,
    "entry_tf_ms": 900_000,
    "context_tf_ms": 3_600_000,
    "higher_tf_ms": 14_400_000,
}

CONFIGS: dict[str, list[dict]] = {
    "H1": [
        {"id": "h1a", "strategy": "range_v2_passive", "params": {"passive_entry_bars": 4}, "label": "N=4 edge"},
        {"id": "h1b", "strategy": "range_v2_passive", "params": {"passive_entry_bars": 2}, "label": "N=2 edge"},
        {"id": "h1c", "strategy": "range_v2_passive", "params": {"passive_entry_bars": 8}, "label": "N=8 edge"},
        {"id": "h1d", "strategy": "range_v2_passive", "params": {"passive_entry_bars": 4, "passive_entry_price": "centre"}, "label": "N=4 centre"},
    ],
    "H2": [
        {"id": "h2a", "strategy": "range_v1", "params": {}, "ratio": 4.0, "label": "ratio 4"},
        {"id": "h2b", "strategy": "range_v1", "params": {}, "ratio": 5.0, "label": "ratio 5"},
        {"id": "h2c", "strategy": "range_v1", "params": {}, "ratio": 6.0, "label": "ratio 6"},
        {"id": "h2d", "strategy": "range_v1", "params": {}, "ratio": 8.0, "label": "ratio 8"},
    ],
    "H3": [
        {"id": "h3a", "strategy": "range_v1", "params": {"time_stop_bars": 32}, "entry_tf_ms": 3_600_000, "context_tf_ms": 3_600_000, "higher_tf_ms": 14_400_000, "label": "1h, 32 bars"},
        {"id": "h3b", "strategy": "range_v1", "params": {"time_stop_bars": 8}, "entry_tf_ms": 3_600_000, "context_tf_ms": 3_600_000, "higher_tf_ms": 14_400_000, "label": "1h, 8 bars"},
    ],
}

RANDOM = {"id": "random", "strategy": "random_entry", "params": {"entry_probability": 0.02, "seed": 42}}


def _summary(trades: pl.DataFrame, signals: pl.DataFrame) -> dict:
    if trades.height == 0:
        enter = signals.filter(pl.col("action") == "ENTER").height if signals.height else 0
        cancel = signals.filter(pl.col("action") == "CANCEL").height if signals.height else 0
        return {
            "trades": 0, "net_pnl": 0.0, "gross_pnl": 0.0, "fees": 0.0, "funding": 0.0,
            "profit_factor": None, "expectancy_r": None, "win_rate": None,
            "ci90": [None, None], "r_values": [], "net_values": [],
            "enter_signals": enter, "cancel_signals": cancel, "fill_rate": 0.0 if enter else None,
        }
    r = trades["net_r"].to_numpy().astype(float)
    net = trades["net_pnl"].to_numpy().astype(float)
    wins = float(net[net > 0].sum())
    losses = float(-net[net < 0].sum())
    mean, lo, hi = bootstrap_mean_ci(r, alpha=0.10)
    enter = signals.filter(pl.col("action") == "ENTER").height if signals.height else trades.height
    cancel = signals.filter(pl.col("action") == "CANCEL").height if signals.height else 0
    return {
        "trades": trades.height,
        "net_pnl": float(net.sum()),
        "gross_pnl": float(trades["gross_pnl"].sum()),
        "fees": float(trades["fees"].sum()),
        "funding": float(trades["funding"].sum()),
        "profit_factor": wins / losses if losses > 0 else None,
        "expectancy_r": float(mean),
        "win_rate": float((r > 0).mean()),
        "ci90": [float(lo), float(hi)],
        "r_values": [float(x) for x in r],
        "net_values": [float(x) for x in net],
        "enter_signals": int(enter),
        "cancel_signals": int(cancel),
        "fill_rate": float(trades.height / enter) if enter else None,
    }


def _run(task: dict) -> dict:
    spec = task["spec"]
    fold = task["fold"]
    cfg = BacktestConfig(
        strategy=spec["strategy"],
        strategy_params=spec.get("params", {}),
        symbols=task["symbols"],
        start_ms=fold[2],
        end_ms=fold[3],
        journal=False,
        enrich=False,
        commission_multiplier=task.get("commission_multiplier", 1.0),
        slippage_multiplier=task.get("slippage_multiplier", 1.0),
        entry_tf_ms=spec.get("entry_tf_ms", 900_000),
        context_tf_ms=spec.get("context_tf_ms", 3_600_000),
        higher_tf_ms=spec.get("higher_tf_ms", 14_400_000),
    )
    if spec.get("ratio"):
        cfg = replace(cfg, range_score=RangeScoreConfig(min_width_cost_ratio=spec["ratio"]))
    res = run_backtest(cfg)
    return {
        "id": spec["id"],
        "label": spec.get("label", ""),
        "fold": fold[0],
        "run_id": res.run_id,
        "run_dir": str(res.run_dir),
        "summary": _summary(res.trades, res.signals),
        "cost": {"commission_multiplier": cfg.commission_multiplier, "slippage_multiplier": cfg.slippage_multiplier},
    }


def _aggregate(rows: list[dict]) -> dict:
    rows = sorted(rows, key=lambda r: r["fold"])
    r = np.array([x for row in rows for x in row["summary"]["r_values"]], dtype=float)
    net_values = [x for row in rows for x in row["summary"]["net_values"]]
    net = np.array(net_values, dtype=float)
    if len(r) == 0:
        return {"trades": 0, "net_pnl": 0.0, "profit_factor": None, "expectancy_r": None,
                "ci90": [None, None], "win_rate": None, "positive_folds": 0, "folds": [x["fold"] for x in rows],
                "fold_net": [0.0 for _ in rows], "fees": 0.0, "funding": 0.0, "max_dd_usdt": 0.0,
                "enter_signals": sum(x["summary"]["enter_signals"] for x in rows),
                "cancel_signals": sum(x["summary"]["cancel_signals"] for x in rows),
                "fill_rate": None}
    mean, lo, hi = bootstrap_mean_ci(r, alpha=0.10)
    wins = float(net[net > 0].sum())
    losses = float(-net[net < 0].sum())
    cum = np.cumsum(net)
    peak = np.maximum.accumulate(cum)
    dd = float((peak - cum).max()) if len(cum) else 0.0
    return {
        "trades": len(r),
        "net_pnl": float(net.sum()),
        "profit_factor": wins / losses if losses > 0 else None,
        "expectancy_r": float(mean),
        "ci90": [float(lo), float(hi)],
        "win_rate": float((r > 0).mean()),
        "positive_folds": sum(1 for row in rows if row["summary"]["net_pnl"] > 0),
        "folds": [x["fold"] for x in rows],
        "fold_net": [round(x["summary"]["net_pnl"], 4) for x in rows],
        "fees": float(sum(x["summary"]["fees"] for x in rows)),
        "funding": float(sum(x["summary"]["funding"] for x in rows)),
        "max_dd_usdt": dd,
        "enter_signals": int(sum(x["summary"]["enter_signals"] for x in rows)),
        "cancel_signals": int(sum(x["summary"]["cancel_signals"] for x in rows)),
        "fill_rate": (sum(x["summary"]["trades"] for x in rows) / sum(x["summary"]["enter_signals"] for x in rows))
        if sum(x["summary"]["enter_signals"] for x in rows) else None,
    }


def _run_batch(tasks: list[dict], workers: int) -> list[dict]:
    rows: list[dict] = []
    with ProcessPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_run, t): t for t in tasks}
        for fut in as_completed(futures):
            task = futures[fut]
            try:
                rows.append(fut.result())
            except Exception as exc:  # a failed trial is still a trial
                rows.append({"id": task["spec"]["id"], "fold": task["fold"][0], "error": str(exc), "summary": None})
    return rows


def _record(rows: list[dict], hypothesis: str, phase: str, data_slice: str, spec: dict) -> None:
    for row in rows:
        record_experiment(
            {
                "round": "research_round1",
                "phase": phase,
                "hypothesis": hypothesis,
                "branch": "eval/cost-fix",
                "data_slice": f"{data_slice}:{row['fold']}",
                "start_ms": _ms(*map(int, row["fold"].split("-")[:2])),
                "end_ms": None,
                "symbols": 20,
                "config_hash": spec.get("id", ""),
                "run_id": row.get("run_id"),
                "params_json": spec,
                "metrics_json": row.get("summary") or {"error": row.get("error")},
                "notes": f"{phase} {row['fold']}",
            }
        )


def _plateau(config_id: str, aggregate: dict[str, dict], hypothesis: str) -> bool:
    others = [(cid, agg) for cid, agg in aggregate.items() if cid != config_id and agg.get("trades")]
    if not others:
        return False
    if hypothesis == "H3":
        return all(agg["expectancy_r"] > 0 for _, agg in others)
    positive = sum(1 for _, agg in others if agg["expectancy_r"] > 0)
    return positive * 2 >= len(others)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hypothesis", default="all", choices=["all", "H1", "H2", "H3"])
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--symbols", type=int, default=20)
    args = ap.parse_args()
    paths = Paths()
    universe = sorted(paths.universe.glob("universe_*.parquet"))[-1]
    symbols = pl.read_parquet(universe).filter(pl.col("in_universe")).sort("rank")["symbol"].to_list()[: args.symbols]
    OUT.mkdir(parents=True, exist_ok=True)
    result: dict = {"generated": datetime.now(UTC).isoformat(), "symbols": symbols,
                    "folds": [{"start": f[0], "end": f[1]} for f in FOLDS], "hypotheses": {}}

    baseline_rows = _run_batch([{"spec": BASE, "fold": f, "symbols": symbols} for f in FOLDS], args.workers)
    _record(baseline_rows, "baseline", "baseline_walk_forward", "development", BASE)
    baseline = _aggregate([r for r in baseline_rows if r.get("summary")])
    result["baseline"] = baseline

    random_rows = _run_batch([{"spec": RANDOM, "fold": f, "symbols": symbols} for f in FOLDS], args.workers)
    _record(random_rows, "random", "benchmark_random", "development", RANDOM)
    random_agg = _aggregate([r for r in random_rows if r.get("summary")])
    result["random"] = random_agg

    names = ["H1", "H2", "H3"] if args.hypothesis == "all" else [args.hypothesis]
    for name in names:
        specs = CONFIGS[name]
        tasks = [{"spec": s, "fold": f, "symbols": symbols} for s in specs for f in FOLDS]
        rows = _run_batch(tasks, args.workers)
        for spec in specs:
            spec_rows = [r for r in rows if r["id"] == spec["id"] and r.get("summary")]
            _record(spec_rows, name, "hypothesis_fold", "development", spec)
        aggregates = {}
        for spec in specs:
            spec_rows = [r for r in rows if r["id"] == spec["id"] and r.get("summary")]
            if spec_rows:
                aggregates[spec["id"]] = _aggregate(spec_rows)
        for cid, agg in aggregates.items():
            record_experiment(
                {
                    "round": "research_round1", "phase": "hypothesis_aggregate", "hypothesis": name,
                    "branch": "eval/cost-fix", "data_slice": "development_wf",
                    "symbols": len(symbols), "params_json": next(s for s in specs if s["id"] == cid),
                    "metrics_json": agg, "notes": "four-fold aggregate",
                }
            )
        best_id = max(aggregates, key=lambda c: (aggregates[c]["expectancy_r"] or -99, aggregates[c]["trades"])) if aggregates else None
        stress = None
        if best_id:
            best_spec = next(s for s in specs if s["id"] == best_id)
            stress_tasks = [
                {"spec": best_spec, "fold": f, "symbols": symbols, "commission_multiplier": 1.5, "slippage_multiplier": 2.0}
                for f in FOLDS
            ]
            stress_rows = _run_batch(stress_tasks, args.workers)
            _record(stress_rows, name, "hypothesis_stress_fold", "development", {**best_spec, "stress": True})
            stress = _aggregate([r for r in stress_rows if r.get("summary")])
            record_experiment(
                {
                    "round": "research_round1", "phase": "hypothesis_stress_aggregate", "hypothesis": name,
                    "branch": "eval/cost-fix", "data_slice": "development_wf",
                    "symbols": len(symbols), "params_json": {**best_spec, "stress": True},
                    "metrics_json": stress, "notes": "four-fold aggregate, commission x1.5 slippage x2",
                }
            )
        gates = {}
        for cid, agg in aggregates.items():
            gates[cid] = {
                "trades_ge_100": agg["trades"] >= 100,
                "pf_ge_1_10": bool(agg["profit_factor"] and agg["profit_factor"] >= 1.10),
                "ci90_above_baseline": bool(agg["ci90"][0] is not None and baseline["expectancy_r"] is not None and agg["ci90"][0] > baseline["expectancy_r"]),
                "beats_random": bool(agg["expectancy_r"] is not None and random_agg["expectancy_r"] is not None and agg["expectancy_r"] > random_agg["expectancy_r"]),
                "plateau": _plateau(cid, aggregates, name),
            }
            gates[cid]["verdict"] = all(gates[cid].values())
            if cid == best_id and stress:
                gates[cid]["pf_stress_ge_1_00"] = bool(stress["profit_factor"] and stress["profit_factor"] >= 1.00)
                gates[cid]["verdict"] = gates[cid]["verdict"] and gates[cid]["pf_stress_ge_1_00"]
        result["hypotheses"][name] = {
            "configs": aggregates,
            "gates": gates,
            "best": best_id,
            "stress": stress,
            "rows": [{k: v for k, v in r.items() if k != "summary"} | {"summary": r.get("summary")} for r in rows],
        }
        print(f"{name}: best={best_id} verdict={gates.get(best_id, {}).get('verdict')}")

    (OUT / "summary.json").write_text(json.dumps(result, indent=2, default=str))
    print(f"wrote {OUT/'summary.json'}")


if __name__ == "__main__":
    main()
