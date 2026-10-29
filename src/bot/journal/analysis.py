"""Analysis helpers used by the dashboard and kaizen reports."""

from __future__ import annotations

import polars as pl


def calibration(trades: pl.DataFrame, buckets: int = 10) -> pl.DataFrame:
    """Score deciles vs realized net R, with sample counts."""
    df = trades.filter(pl.col("setup_score").is_not_null())
    if df.height < buckets:
        return pl.DataFrame()
    df = df.with_columns(
        pl.col("setup_score").qcut(buckets, labels=[f"D{i + 1}" for i in range(buckets)], allow_duplicates=True).alias("decile")
    )
    return (
        df.group_by("decile")
        .agg(
            pl.len().alias("trades"),
            pl.col("setup_score").mean().alias("mean_score"),
            pl.col("net_r").mean().alias("mean_r"),
            pl.col("net_r").median().alias("median_r"),
            (pl.col("net_r") > 0).mean().alias("win_rate"),
            pl.col("net_pnl").sum().alias("net_pnl"),
        )
        .sort("decile")
    )


def payoff_stats(trades: pl.DataFrame) -> dict:
    if trades.height == 0:
        return {}
    r = trades["net_r"].to_numpy()
    wins = r[r > 0]
    losses = r[r < 0]
    return {
        "trades": int(trades.height),
        "mean_r": float(r.mean()),
        "median_r": float(sorted(r)[len(r) // 2]),
        "std_r": float(r.std(ddof=1)) if len(r) > 1 else None,
        "win_rate": float((r > 0).mean()),
        "avg_win_r": float(wins.mean()) if len(wins) else None,
        "avg_loss_r": float(losses.mean()) if len(losses) else None,
        "payoff_ratio": float(wins.mean() / abs(losses.mean())) if len(wins) and len(losses) else None,
        "profit_factor": float(wins.sum() / abs(losses.sum())) if len(losses) and losses.sum() != 0 else None,
    }


def breakdown(trades: pl.DataFrame, by: str) -> pl.DataFrame:
    if trades.height == 0 or by not in trades.columns:
        return pl.DataFrame()
    return (
        trades.group_by(by)
        .agg(
            pl.len().alias("trades"),
            pl.col("net_pnl").sum().alias("net_pnl"),
            pl.col("net_r").mean().alias("expectancy_r"),
            (pl.col("net_r") > 0).mean().alias("win_rate"),
        )
        .sort("net_pnl", descending=True)
    )


def hourly(trades: pl.DataFrame) -> pl.DataFrame:
    if trades.height == 0:
        return pl.DataFrame()
    return breakdown(trades.with_columns(((pl.col("entry_ts_ms") // 3_600_000) % 24).alias("hour")), "hour").sort("hour")


def error_pareto(trades: pl.DataFrame, tags: pl.DataFrame) -> pl.DataFrame:
    if trades.height == 0 or tags.height == 0:
        return pl.DataFrame()
    joined = tags.join(trades.select(["trade_id", "net_pnl", "net_r"]), on="trade_id", how="inner")
    if joined.height == 0:
        return pl.DataFrame()
    return (
        joined.group_by("tag")
        .agg(
            pl.len().alias("count"),
            pl.col("net_r").sum().alias("total_r"),
            pl.col("net_r").mean().alias("avg_r"),
            pl.col("net_pnl").sum().alias("net_pnl"),
        )
        .sort("total_r")
    )


def score_quantile_table(trades: pl.DataFrame, quantiles: int = 5) -> pl.DataFrame:
    df = trades.filter(pl.col("setup_score").is_not_null())
    if df.height < quantiles:
        return pl.DataFrame()
    return (
        df.with_columns(pl.col("setup_score").qcut(quantiles, labels=[f"Q{i + 1}" for i in range(quantiles)]).alias("q"))
        .group_by("q")
        .agg(pl.len().alias("n"), pl.col("net_r").mean().alias("mean_r"), (pl.col("net_r") > 0).mean().alias("win_rate"))
        .sort("q")
    )
