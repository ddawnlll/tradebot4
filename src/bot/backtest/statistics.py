"""Statistical uncertainty for trade sequences.

A backtest is a sample. This module reports confidence intervals and the
multiple-testing-aware probability that the observed Sharpe is real, instead
of treating a point estimate as truth.
"""

from __future__ import annotations

import math

import numpy as np


def bootstrap_means(values: np.ndarray, n_boot: int = 10_000, seed: int = 7) -> np.ndarray:
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(values), size=(n_boot, len(values)))
    return values[idx].mean(axis=1)


def bootstrap_mean_ci(values: np.ndarray, n_boot: int = 10_000, alpha: float = 0.05, seed: int = 7) -> tuple[float, float, float]:
    if len(values) == 0:
        return (float("nan"), float("nan"), float("nan"))
    means = bootstrap_means(values, n_boot, seed)
    return float(values.mean()), float(np.quantile(means, alpha / 2)), float(np.quantile(means, 1 - alpha / 2))


def block_bootstrap_mean_ci(values: np.ndarray, block: int = 5, n_boot: int = 10_000, alpha: float = 0.05, seed: int = 7) -> tuple[float, float, float]:
    """Circular block bootstrap for autocorrelated trade outcomes."""
    n = len(values)
    if n == 0:
        return (float("nan"), float("nan"), float("nan"))
    block = max(1, min(block, n))
    rng = np.random.default_rng(seed)
    n_blocks = math.ceil(n / block)
    starts = rng.integers(0, n, size=(n_boot, n_blocks))
    idx = (starts[:, :, None] + np.arange(block)[None, None, :]) % n
    samples = values[idx.reshape(n_boot, -1)[:, :n]]
    means = samples.mean(axis=1)
    return float(values.mean()), float(np.quantile(means, alpha / 2)), float(np.quantile(means, 1 - alpha / 2))


def probabilistic_sharpe_ratio(returns: np.ndarray, benchmark_sr: float = 0.0) -> float | None:
    """P(true SR > benchmark) using the Bailey-Lopez de Prado approximation."""
    n = len(returns)
    if n < 5:
        return None
    sr = returns.mean() / returns.std(ddof=1) if returns.std(ddof=1) > 0 else 0.0
    skew = float(((returns - returns.mean()) ** 3).mean() / (returns.std(ddof=1) ** 3)) if returns.std(ddof=1) > 0 else 0.0
    kurt = float(((returns - returns.mean()) ** 4).mean() / (returns.std(ddof=1) ** 4)) if returns.std(ddof=1) > 0 else 3.0
    denom = math.sqrt(max(1e-12, 1 - skew * sr + (kurt - 1) / 4 * sr**2))
    from scipy.stats import norm

    return float(norm.cdf((sr - benchmark_sr) * math.sqrt(n - 1) / denom))


def deflated_sharpe_ratio(returns: np.ndarray, n_trials: int) -> float | None:
    """PSR against the expected maximum Sharpe of ``n_trials`` independent tests."""
    n = len(returns)
    if n < 5 or n_trials < 1:
        return None
    from scipy.stats import norm

    sr = returns.mean() / returns.std(ddof=1) if returns.std(ddof=1) > 0 else 0.0
    expected_max = math.sqrt(2 * math.log(max(n_trials, 2))) - (
        math.log(math.log(max(n_trials, 2))) + math.log(4 * math.pi)
    ) / (2 * math.sqrt(2 * math.log(max(n_trials, 2))))
    return probabilistic_sharpe_ratio(returns, benchmark_sr=expected_max)


def trade_r_ci(trades) -> dict:
    if trades.height == 0:
        return {}
    r = trades["net_r"].to_numpy()
    mean, lo, hi = bootstrap_mean_ci(r)
    bmean, blo, bhi = block_bootstrap_mean_ci(r)
    means = bootstrap_means(r)
    return {
        "mean_r": mean,
        "mean_r_ci95": [lo, hi],
        "mean_r_block_ci95": [blo, bhi],
        "prob_mean_r_positive": float((means > 0).mean()),
        "trades": int(len(r)),
        "std_r": float(r.std(ddof=1)) if len(r) > 1 else None,
    }
