"""Validation suite: walk-forward, sensitivity, benchmarks, ablations, OOS.

Order of evaluation is fixed before running:
1. champion (frozen defaults) on the development period,
2. walk-forward stability inside development,
3. parameter sensitivity inside development,
4. ablations and benchmarks inside development,
5. out-of-sample (last ~25-30%) once,
6. vault (final months) once, only if OOS is not a clear NO_GO.

No parameter is selected using OOS or vault results.
"""

from __future__ import annotations

import json
import logging
import os
from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict, dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path

import polars as pl

from bot.backtest.monte_carlo import MonteCarloConfig, run_monte_carlo
from bot.backtest.runner import BacktestConfig, BacktestResult, run_backtest
from bot.backtest.statistics import deflated_sharpe_ratio, probabilistic_sharpe_ratio, trade_r_ci
from bot.core.market.scoring import RangeScoreConfig
from bot.data.storage import Paths

log = logging.getLogger(__name__)


@dataclass(slots=True)
class ValidationConfig:
    base: BacktestConfig
    dev_end_ms: int
    oos_end_ms: int
    vault_end_ms: int
    walk_forward_windows: int = 4
    walk_forward_symbols: int = 20
    sensitivity_symbols: int = 12
    mc_paths: int = 10_000
    max_workers: int = 4
    out_dir: Path | None = None


def _run_one(cfg: BacktestConfig) -> dict:
    res = run_backtest(cfg)
    return {
        "run_id": res.run_id,
        "run_dir": str(res.run_dir),
        "metrics": _clean(res.metrics),
        "trades": res.trades.height,
        "net_r_values": res.trades["net_r"].to_list() if res.trades.height else [],
        "config_hash": res.run_id.split("_")[-1],
        "elapsed_s": res.elapsed_s,
    }


def _clean(metrics: dict) -> dict:
    drop = {"by_symbol", "by_exit_reason", "by_hour"}
    return {k: v for k, v in metrics.items() if k not in drop}


def _parallel(cfgs: list[BacktestConfig], max_workers: int) -> list[dict]:
    if len(cfgs) == 1 or max_workers <= 1:
        return [_run_one(c) for c in cfgs]
    out: list[dict] = []
    with ProcessPoolExecutor(max_workers=max_workers) as pool:
        futures = [pool.submit(_run_one, c) for c in cfgs]
        for f in futures:
            out.append(f.result())
    return out


def run_validation(cfg: ValidationConfig, paths: Paths | None = None) -> dict:
    paths = paths or Paths()
    out_dir = cfg.out_dir or (paths.reports / f"validation_{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}")
    out_dir.mkdir(parents=True, exist_ok=True)
    base = cfg.base
    report: dict = {
        "generated": datetime.now(UTC).isoformat(),
        "base_config": base.as_dict(),
        "periods": {
            "dev": [base.start_ms, cfg.dev_end_ms],
            "oos": [cfg.dev_end_ms, cfg.oos_end_ms],
            "vault": [cfg.oos_end_ms, cfg.vault_end_ms],
        },
    }

    # 1. champion on development
    log.info("champion dev run")
    dev_cfg = replace(base, end_ms=cfg.dev_end_ms)
    champion = _run_one(dev_cfg)
    report["champion_dev"] = champion
    mc = run_monte_carlo(
        _trades_from(champion),
        MonteCarloConfig(paths=cfg.mc_paths, risk_fraction=base.risk_fraction, starting_equity=base.starting_balance),
    )
    report["monte_carlo"] = mc

    # 2. walk-forward: consecutive windows, same params
    log.info("walk-forward")
    span = cfg.dev_end_ms - base.start_ms
    step = span // cfg.walk_forward_windows
    wf_cfgs = []
    for i in range(cfg.walk_forward_windows):
        wf_cfgs.append(
            replace(
                base,
                start_ms=base.start_ms + i * step,
                end_ms=base.start_ms + (i + 1) * step if i < cfg.walk_forward_windows - 1 else cfg.dev_end_ms,
                symbols=base.symbols[: cfg.walk_forward_symbols],
                journal=False,
                enrich=False,
            )
        )
    report["walk_forward"] = _parallel(wf_cfgs, cfg.max_workers)

    # 3. sensitivity (dev subset)
    log.info("sensitivity")
    sub = replace(base, end_ms=cfg.dev_end_ms, symbols=base.symbols[: cfg.sensitivity_symbols], journal=False, enrich=False)
    variants: list[tuple[str, BacktestConfig]] = []
    for value in (0.45, 0.55, 0.65):
        variants.append((f"min_score={value}", replace(sub, range_score=replace(base.range_score, min_score=value))))
    for value in (0.25, 0.45):
        variants.append((f"stop_buffer_atr={value}", replace(sub, range_score=replace(base.range_score, stop_buffer_atr=value))))
    for value in (0.15, 0.35):
        variants.append((f"rejection_wick_frac={value}", replace(sub, strategy_params={**base.strategy_params, "rejection_wick_frac": value})))
    for value in (24, 48):
        variants.append((f"time_stop_bars={value}", replace(sub, strategy_params={**base.strategy_params, "time_stop_bars": value})))
    for value in (0.25, 0.50):
        variants.append((f"breakout_exit_atr={value}", replace(sub, strategy_params={**base.strategy_params, "breakout_exit_atr": value})))
    sens = _parallel([c for _, c in variants], cfg.max_workers)
    report["sensitivity"] = [
        {"variant": name, **_summary(res)} for (name, _), res in zip(variants, sens, strict=True)
    ]

    # 4. ablations and benchmarks (same subset for comparability)
    log.info("ablations and benchmarks")
    ablations: list[tuple[str, BacktestConfig]] = [
        ("regime_filter_off", replace(sub, strategy_params={**base.strategy_params, "require_range_regime": False})),
        ("higher_tf_filter_off", replace(sub, strategy_params={**base.strategy_params, "respect_higher_tf": False})),
        ("breakout_exit_off", replace(sub, strategy_params={**base.strategy_params, "enable_breakout_exit": False})),
        ("single_tp", replace(sub, two_leg_tp=False)),
        ("trend_pullback_benchmark", replace(sub, strategy="trend_pullback", strategy_params={})),
        ("random_entry_benchmark", replace(sub, strategy="random_entry", strategy_params={})),
    ]
    abl = _parallel([c for _, c in ablations], cfg.max_workers)
    report["ablations"] = [
        {"variant": name, **_summary(res)} for (name, _), res in zip(ablations, abl, strict=True)
    ]

    # 5. OOS once
    log.info("out-of-sample")
    oos_cfg = replace(base, start_ms=cfg.dev_end_ms, end_ms=cfg.oos_end_ms)
    oos = _run_one(oos_cfg)
    report["oos"] = oos
    report["oos_ci"] = trade_r_ci(_trades_from(oos))
    r = _r_array(oos)
    report["oos_psr"] = probabilistic_sharpe_ratio(r) if len(r) else None
    report["oos_dsr"] = deflated_sharpe_ratio(r, n_trials=1 + len(variants) + len(ablations)) if len(r) else None

    # 6. vault only if OOS is not a clear failure
    if _oos_passes_core(oos):
        log.info("vault (OOS passed core criteria)")
        vault_cfg = replace(base, start_ms=cfg.oos_end_ms, end_ms=cfg.vault_end_ms)
        report["vault"] = _run_one(vault_cfg)
    else:
        report["vault"] = {"skipped": "OOS failed core criteria; vault not opened"}

    report["go_no_go"] = go_no_go(report)
    (out_dir / "validation.json").write_text(json.dumps(report, indent=2, default=str))
    (out_dir / "validation.md").write_text(render_report(report))
    return report


def _summary(res: dict) -> dict:
    m = res["metrics"]
    return {
        "trades": res["trades"],
        "net_pnl": m.get("net_pnl"),
        "expectancy_r": m.get("expectancy_r"),
        "profit_factor": m.get("profit_factor"),
        "win_rate": m.get("win_rate"),
        "max_drawdown_pct": m.get("max_drawdown_pct"),
        "return_pct": m.get("return_pct"),
        "run_id": res["run_id"],
    }


def _trades_from(res: dict):
    p = Path(res["run_dir"]) / "trades.parquet"
    return pl.read_parquet(p) if p.exists() else pl.DataFrame({"net_r": []})


def _r_array(res: dict):
    import numpy as np

    return np.array(res.get("net_r_values", []), dtype=float)


def _oos_passes_core(oos: dict) -> bool:
    m = oos["metrics"]
    pf = m.get("profit_factor")
    exp = m.get("expectancy_r")
    return bool(pf and pf > 1.0 and exp and exp > 0 and oos["trades"] >= 30)


def go_no_go(report: dict) -> dict:
    oos = report.get("oos", {}).get("metrics", {})
    wf = report.get("walk_forward", [])
    sens = report.get("sensitivity", [])
    abl = {a["variant"]: a for a in report.get("ablations", [])}
    oos_pf = oos.get("profit_factor")
    oos_exp = oos.get("expectancy_r")
    oos_dd = oos.get("max_drawdown_pct")
    wf_positive = sum(1 for w in wf if (w["metrics"].get("net_pnl") or 0) > 0)
    sens_positive = sum(1 for s in sens if (s.get("expectancy_r") or 0) > 0)
    rand = abl.get("random_entry_benchmark", {})
    trend = abl.get("trend_pullback_benchmark", {})
    rnd_exp = rand.get("expectancy_r")
    criteria = {
        "oos_trades_ge_100": (report.get("oos", {}).get("trades") or 0) >= 100,
        "oos_profit_factor_gt_1_2": bool(oos_pf and oos_pf > 1.2),
        "oos_positive_expectancy": bool(oos_exp and oos_exp > 0),
        "oos_max_dd_le_30pct": bool(oos_dd is not None and oos_dd <= 30.0),
        "walk_forward_majority_positive": bool(wf and wf_positive * 2 > len(wf)),
        "sensitivity_majority_positive": bool(sens and sens_positive * 2 > len(sens)),
        "beats_random_entry": bool(oos_exp is not None and rnd_exp is not None and oos_exp > rnd_exp),
        "beats_trend_pullback": bool(oos_exp is not None and trend.get("expectancy_r") is not None and oos_exp > trend["expectancy_r"]),
        "positive_ci_lower_bound": bool(report.get("oos_ci", {}).get("mean_r_block_ci95", [None])[0] is not None and report["oos_ci"]["mean_r_block_ci95"][0] > 0),
    }
    verdict = "GO" if all(criteria.values()) else "NO_GO"
    reasons = [k for k, v in criteria.items() if not v]
    return {"verdict": verdict, "criteria": criteria, "failed": reasons}


def render_report(report: dict) -> str:
    lines = ["# Validation report", "", f"Generated: {report['generated']}", ""]
    lines += ["## Champion (dev)", "", _table([_summary(report["champion_dev"])]), ""]
    lines += ["## Walk-forward", "", _table(report["walk_forward"] and [_summary(x) for x in report["walk_forward"]]), ""]
    lines += ["## Sensitivity", "", _table(report["sensitivity"]), ""]
    lines += ["## Ablations and benchmarks", "", _table(report["ablations"]), ""]
    lines += ["## Out of sample", "", _table([_summary(report["oos"])]), ""]
    mc = report.get("monte_carlo", {})
    if mc:
        lines += ["## Monte Carlo (iid / block)", "", "```json", json.dumps(mc, indent=2), "```", ""]
    lines += ["## Go / No-Go", "", "```json", json.dumps(report["go_no_go"], indent=2), "```", ""]
    if "vault" in report:
        lines += ["## Vault", "", "```json", json.dumps(report["vault"], indent=2, default=str)[:4000], "```", ""]
    return "\n".join(lines)


def _table(rows: list[dict]) -> str:
    if not rows:
        return "_no data_"
    keys = list(rows[0].keys())
    out = ["| " + " | ".join(keys) + " |", "|" + "|".join(["---"] * len(keys)) + "|"]
    for r in rows:
        vals = []
        for k in keys:
            v = r.get(k)
            vals.append(f"{v:.3f}" if isinstance(v, float) else str(v))
        out.append("| " + " | ".join(vals) + " |")
    return "\n".join(out)
