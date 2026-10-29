"""Backtest performance metrics.

All metrics are computed from realized trades and the simulated account equity.
Nothing here re-prices trades: costs, fees, funding and slippage already come
from the execution engine and the core accounting.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import polars as pl


@dataclass(slots=True)
class MetricsConfig:
    periods_per_year: float = 365.25
    min_trades_for_confidence: int = 30


def compute_metrics(trades: pl.DataFrame, equity: pl.DataFrame, starting_balance: float) -> dict:
    out: dict = {
        "starting_balance": starting_balance,
        "trades": 0,
        "net_pnl": 0.0,
        "return_pct": 0.0,
        "final_equity": starting_balance,
        "max_drawdown_pct": 0.0,
        "max_drawdown_usdt": 0.0,
        "profit_factor": None,
        "win_rate": None,
        "expectancy_r": None,
        "avg_r": None,
        "median_r": None,
        "sharpe": None,
        "sortino": None,
        "calmar": None,
        "fees": 0.0,
        "funding": 0.0,
        "slippage": 0.0,
        "long": {},
        "short": {},
        "by_symbol": [],
        "by_exit_reason": [],
        "by_hour": [],
        "avg_holding_hours": None,
        "exposure_pct": None,
    }
    eq = _equity_curve(equity, starting_balance)
    if eq.height > 1:
        final = float(eq["equity"][-1])
        out["final_equity"] = final
        out["return_pct"] = (final / starting_balance - 1.0) * 100.0
        dd = eq["drawdown_pct"].to_numpy()
        out["max_drawdown_pct"] = float(np.nanmin(dd)) * -100.0 if len(dd) else 0.0
        out["max_drawdown_usdt"] = float((eq["peak"] - eq["equity"]).max())
        out["sharpe"] = _sharpe(eq)
        out["sortino"] = _sortino(eq)
        years = max((eq["ts_ms"][-1] - eq["ts_ms"][0]) / (365.25 * 86_400_000), 1e-9)
        cagr = (final / starting_balance) ** (1 / years) - 1 if final > 0 else -1.0
        out["cagr_pct"] = cagr * 100.0
        out["calmar"] = (cagr * 100.0) / out["max_drawdown_pct"] if out["max_drawdown_pct"] > 0 else None
        out["exposure_pct"] = _exposure(eq)

    if trades.height == 0:
        return out

    net = trades["net_pnl"].to_numpy()
    r = trades["net_r"].to_numpy()
    wins = net[net > 0]
    losses = net[net < 0]
    out["trades"] = int(trades.height)
    out["net_pnl"] = float(net.sum())
    out["fees"] = float(trades["fees"].sum())
    out["funding"] = float(trades["funding"].sum())
    out["slippage"] = float(trades["slippage"].sum())
    out["profit_factor"] = float(wins.sum() / -losses.sum()) if losses.sum() < 0 else (math.inf if wins.sum() > 0 else None)
    out["win_rate"] = float((net > 0).mean())
    out["expectancy_r"] = float(r.mean())
    out["avg_r"] = float(r.mean())
    out["median_r"] = float(np.median(r))
    out["std_r"] = float(np.std(r, ddof=1)) if len(r) > 1 else None
    out["avg_holding_hours"] = float(((trades["exit_ts_ms"] - trades["entry_ts_ms"]) / 3_600_000).mean())

    for side in ("LONG", "SHORT"):
        sub = trades.filter(pl.col("side") == side)
        out[side.lower()] = _side_stats(sub)

    out["by_symbol"] = _group(trades, "symbol")
    out["by_exit_reason"] = _group(trades, "exit_reason")
    hours = ((trades["entry_ts_ms"] // 3_600_000) % 24).alias("hour")
    out["by_hour"] = _group(trades.with_columns(hours), "hour")
    out["bayes_win_rate_lower"] = _bayes_lower(net)
    return out


def _equity_curve(equity: pl.DataFrame, starting_balance: float) -> pl.DataFrame:
    if equity.height == 0:
        return pl.DataFrame({"ts_ms": [], "equity": [], "peak": [], "drawdown_pct": []})
    eq = equity.select(["ts_ms", "equity"]).sort("ts_ms")
    eq = eq.with_columns(pl.col("equity").cum_max().alias("peak"))
    eq = eq.with_columns(((pl.col("equity") / pl.col("peak")) - 1.0).alias("drawdown_pct"))
    return eq


def _daily_returns(eq: pl.DataFrame) -> np.ndarray:
    if eq.height < 2:
        return np.array([])
    df = eq.with_columns(pl.from_epoch(pl.col("ts_ms"), time_unit="ms").dt.date().alias("day"))
    daily = df.group_by("day").agg(pl.col("equity").last()).sort("day")
    vals = daily["equity"].to_numpy()
    if len(vals) < 2:
        return np.array([])
    return vals[1:] / vals[:-1] - 1.0


def _sharpe(eq: pl.DataFrame) -> float | None:
    rets = _daily_returns(eq)
    if len(rets) < 5 or rets.std(ddof=1) <= 0:
        return None
    return float(rets.mean() / rets.std(ddof=1) * math.sqrt(365))


def _sortino(eq: pl.DataFrame) -> float | None:
    rets = _daily_returns(eq)
    if len(rets) < 5:
        return None
    downside = rets[rets < 0]
    if len(downside) < 2 or downside.std(ddof=1) <= 0:
        return None
    return float(rets.mean() / downside.std(ddof=1) * math.sqrt(365))


def _exposure(eq: pl.DataFrame) -> float | None:
    if "used_margin" not in eq.columns:
        return None
    used = eq["used_margin"].to_numpy()
    return float((used > 0).mean() * 100.0)


def _side_stats(trades: pl.DataFrame) -> dict:
    if trades.height == 0:
        return {"trades": 0}
    net = trades["net_pnl"].to_numpy()
    wins = net[net > 0]
    losses = net[net < 0]
    return {
        "trades": int(trades.height),
        "net_pnl": float(net.sum()),
        "win_rate": float((net > 0).mean()),
        "expectancy_r": float(trades["net_r"].mean()),
        "profit_factor": float(wins.sum() / -losses.sum()) if losses.sum() < 0 else None,
        "funding": float(trades["funding"].sum()),
        "tp1_rate": float(trades["tp1_filled"].mean()),
        "stop_rate": float((trades["exit_reason"] == "STOP").mean()),
    }


def _group(trades: pl.DataFrame, col: str) -> list[dict]:
    rows = []
    for key, sub in trades.group_by(col):
        k = key[0] if isinstance(key, tuple) else key
        net = sub["net_pnl"].to_numpy()
        rows.append(
            {
                col: k if not hasattr(k, "isoformat") else k.isoformat(),
                "trades": int(sub.height),
                "net_pnl": float(net.sum()),
                "expectancy_r": float(sub["net_r"].mean()),
                "win_rate": float((net > 0).mean()),
            }
        )
    rows.sort(key=lambda d: d["net_pnl"], reverse=True)
    return rows


def _bayes_lower(net: np.ndarray, alpha: float = 1.0, beta: float = 1.0) -> float | None:
    """Posterior lower bound of win probability (Beta-Binomial, 5% quantile)."""
    if len(net) == 0:
        return None
    wins = int((net > 0).sum())
    a = alpha + wins
    b = beta + len(net) - wins
    from scipy.stats import beta as beta_dist

    return float(beta_dist.ppf(0.05, a, b))
