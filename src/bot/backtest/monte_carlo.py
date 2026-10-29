"""Monte Carlo resampling of trade outcomes for a 20 USDT account.

Two schemes:
* i.i.d. bootstrap of net R (fast, assumes independent trades),
* circular block bootstrap (preserves short-range dependence between trades).

Equity is compounded per trade with a fixed fractional risk, which is how the
live system sizes positions. Results are distributions, not forecasts.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True, slots=True)
class MonteCarloConfig:
    paths: int = 10_000
    risk_fraction: float = 0.05
    starting_equity: float = 20.0
    target_equity: float = 40.0
    ruin_equity: float = 10.0
    block: int = 5
    seed: int = 11
    horizon_trades: int | None = None


def simulate(r_values: np.ndarray, cfg: MonteCarloConfig | None = None, *, block: bool = False) -> dict:
    cfg = cfg or MonteCarloConfig()
    n = len(r_values)
    if n == 0:
        return {}
    horizon = cfg.horizon_trades or n
    rng = np.random.default_rng(cfg.seed)
    if block and n > 1:
        block_len = max(1, min(cfg.block, n))
        n_blocks = int(np.ceil(horizon / block_len))
        starts = rng.integers(0, n, size=(cfg.paths, n_blocks))
        idx = (starts[:, :, None] + np.arange(block_len)[None, None, :]) % n
        draws = r_values[idx.reshape(cfg.paths, -1)[:, :horizon]]
    else:
        draws = r_values[rng.integers(0, n, size=(cfg.paths, horizon))]

    equity = np.full(cfg.paths, cfg.starting_equity, dtype=float)
    peak = equity.copy()
    max_dd = np.zeros(cfg.paths)
    hit_target = np.full(cfg.paths, -1, dtype=int)
    hit_ruin = np.full(cfg.paths, -1, dtype=int)
    for t in range(horizon):
        risk = equity * cfg.risk_fraction
        equity = np.maximum(equity + draws[:, t] * risk, 0.0)
        peak = np.maximum(peak, equity)
        dd = 1.0 - equity / np.maximum(peak, 1e-9)
        max_dd = np.maximum(max_dd, dd)
        newly = (equity >= cfg.target_equity) & (hit_target < 0)
        hit_target[newly] = t + 1
        ruined = (equity <= cfg.ruin_equity) & (hit_ruin < 0)
        hit_ruin[ruined] = t + 1
        if (hit_target >= 0).all() and (hit_ruin >= 0).all():
            break

    return {
        "paths": cfg.paths,
        "horizon_trades": horizon,
        "prob_target": float((hit_target >= 0).mean()),
        "prob_ruin": float((hit_ruin >= 0).mean()),
        "prob_end_above_start": float((equity > cfg.starting_equity).mean()),
        "median_final_equity": float(np.median(equity)),
        "p05_final_equity": float(np.quantile(equity, 0.05)),
        "p95_final_equity": float(np.quantile(equity, 0.95)),
        "median_max_drawdown_pct": float(np.median(max_dd) * 100),
        "p95_max_drawdown_pct": float(np.quantile(max_dd, 0.95) * 100),
        "median_trades_to_target": _median_positive(hit_target),
        "median_trades_to_ruin": _median_positive(hit_ruin),
        "block_bootstrap": block,
    }


def run_monte_carlo(trades, cfg: MonteCarloConfig | None = None) -> dict:
    if trades is None or trades.height == 0:
        return {}
    r = trades["net_r"].to_numpy().astype(float)
    return {
        "iid": simulate(r, cfg, block=False),
        "block": simulate(r, cfg, block=True),
    }


def _median_positive(x: np.ndarray) -> float | None:
    vals = x[x > 0]
    return float(np.median(vals)) if len(vals) else None
