"""Data loading and transforms for the tradebot dashboard.

Everything in this module is framework independent: it imports polars and the
standard library only, never streamlit. Pages call these functions through the
small caching layer in ``dashboard.cache``.

Run directories are discovered below ``TRADEBOT_RUNS_DIR`` (default
``<TRADEBOT_HOME>/runs``). Market data is read from
``<TRADEBOT_HOME>/data/market/bars_1m/symbol=<SYMBOL>/year=<YYYY>/month=<MM>/data.parquet``.
"""

from __future__ import annotations

import datetime as dt
import json
import math
import os
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

import polars as pl

DASHBOARD_DIR = Path(__file__).resolve().parent
REPO_ROOT = DASHBOARD_DIR.parent

TRADE_SCHEMA: dict[str, pl.DataType] = {
    "trade_id": pl.Utf8,
    "signal_id": pl.Utf8,
    "symbol": pl.Utf8,
    "side": pl.Utf8,
    "entry_price": pl.Float64,
    "exit_price": pl.Float64,
    "quantity": pl.Float64,
    "entry_ts_ms": pl.Int64,
    "exit_ts_ms": pl.Int64,
    "gross_pnl": pl.Float64,
    "fees": pl.Float64,
    "funding": pl.Float64,
    "slippage": pl.Float64,
    "net_pnl": pl.Float64,
    "net_r": pl.Float64,
    "risk_usdt": pl.Float64,
    "mae_r": pl.Float64,
    "mfe_r": pl.Float64,
    "tp1_filled": pl.Boolean,
    "exit_reason": pl.Utf8,
    "strategy_version": pl.Utf8,
    "setup_score": pl.Float64,
    "bars_held": pl.Int64,
}

FILL_SCHEMA: dict[str, pl.DataType] = {
    "ts_ms": pl.Int64,
    "symbol": pl.Utf8,
    "client_order_id": pl.Utf8,
    "kind": pl.Utf8,
    "side": pl.Utf8,
    "price": pl.Float64,
    "qty": pl.Float64,
    "fee": pl.Float64,
    "liquidity": pl.Utf8,
    "order_type": pl.Utf8,
    "signal_id": pl.Utf8,
}

ORDER_SCHEMA: dict[str, pl.DataType] = {
    "ts_ms": pl.Int64,
    "symbol": pl.Utf8,
    "client_order_id": pl.Utf8,
    "kind": pl.Utf8,
    "order_type": pl.Utf8,
    "price": pl.Float64,
    "qty": pl.Float64,
}

EQUITY_SCHEMA: dict[str, pl.DataType] = {
    "ts_ms": pl.Int64,
    "equity": pl.Float64,
    "balance": pl.Float64,
    "used_margin": pl.Float64,
    "unrealized_pnl": pl.Float64,
}

SIGNAL_SCHEMA: dict[str, pl.DataType] = {
    "ts_ms": pl.Int64,
    "signal_id": pl.Utf8,
    "symbol": pl.Utf8,
    "action": pl.Utf8,
    "side": pl.Utf8,
    "reason": pl.Utf8,
    "reject_reason": pl.Utf8,
    "score": pl.Float64,
    "regime": pl.Utf8,
    "regime_confidence": pl.Float64,
    "support": pl.Float64,
    "resistance": pl.Float64,
    "features": pl.Utf8,
}

BAR_SCHEMA: dict[str, pl.DataType] = {
    "symbol": pl.Utf8,
    "ts_open_ms": pl.Int64,
    "ts_close_ms": pl.Int64,
    "open": pl.Float64,
    "high": pl.Float64,
    "low": pl.Float64,
    "close": pl.Float64,
    "volume": pl.Float64,
    "quote_volume": pl.Float64,
    "trades": pl.Int64,
    "taker_buy_volume": pl.Float64,
    "taker_buy_quote_volume": pl.Float64,
}

TAG_SCHEMA: dict[str, pl.DataType] = {
    "trade_id": pl.Utf8,
    "tag": pl.Utf8,
    "source": pl.Utf8,
    "notes": pl.Utf8,
}

RUN_SCHEMA: dict[str, pl.DataType] = {
    "run_id": pl.Utf8,
    "path": pl.Utf8,
    "source": pl.Utf8,
    "strategy": pl.Utf8,
    "strategy_version": pl.Utf8,
    "created": pl.Utf8,
    "trades": pl.Int64,
    "net_pnl": pl.Float64,
    "return_pct": pl.Float64,
    "has_trades": pl.Boolean,
    "has_signals": pl.Boolean,
    "has_fills": pl.Boolean,
    "has_equity": pl.Boolean,
}

HYPOTHETICAL_SCHEMA: dict[str, pl.DataType] = {
    "signal_ts_ms": pl.Int64,
    "symbol": pl.Utf8,
    "side": pl.Utf8,
    "entry_price": pl.Float64,
    "stop_price": pl.Float64,
    "target_price": pl.Float64,
    "risk_per_unit": pl.Float64,
    "outcome": pl.Utf8,
    "r_multiple": pl.Float64,
    "bars_held": pl.Int64,
    "mae_r": pl.Float64,
    "mfe_r": pl.Float64,
    "valid": pl.Boolean,
    "note": pl.Utf8,
}

# Tags that describe an adverse outcome rather than a completed target.
ERROR_TAGS = (
    "stop_out",
    "breakout_against",
    "breakeven_stop",
    "full_stop_risk",
    "gave_back_profit",
    "immediate_adverse",
    "funding_drag",
    "fee_drag",
    "large_loss",
)

_KNOWN_ORDER_KINDS = {"ENTRY", "STOP", "TP1", "TP2", "EXIT"}


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
def tradebot_home(home: str | Path | None = None) -> Path:
    """Resolve the repository/data home directory."""
    if home is not None:
        return Path(home).expanduser().resolve()
    return Path(os.environ.get("TRADEBOT_HOME", REPO_ROOT)).expanduser().resolve()


def runs_dir(root: str | Path | None = None, home: str | Path | None = None) -> Path:
    """Resolve the runs directory honouring TRADEBOT_RUNS_DIR."""
    if root is not None:
        return Path(root).expanduser().resolve()
    env = os.environ.get("TRADEBOT_RUNS_DIR")
    if env:
        candidate = Path(env).expanduser()
        if not candidate.is_absolute():
            candidate = tradebot_home(home) / candidate
        return candidate.resolve()
    return tradebot_home(home) / "runs"


def market_dir(home: str | Path | None = None) -> Path:
    return tradebot_home(home) / "data" / "market" / "bars_1m"


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------
def _empty(schema: Mapping[str, pl.DataType]) -> pl.DataFrame:
    return pl.DataFrame(schema=dict(schema))


def _read_json(path: Path) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as fh:
            payload = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _read_parquet(path: Path, schema: Mapping[str, pl.DataType]) -> pl.DataFrame:
    """Read a parquet file, adding any missing required columns as nulls."""
    if not path.exists():
        return _empty(schema)
    try:
        df = pl.read_parquet(path)
    except Exception:
        return _empty(schema)
    additions = [
        pl.lit(None, dtype=dtype).alias(name)
        for name, dtype in schema.items()
        if name not in df.columns
    ]
    if additions:
        df = df.with_columns(additions)
    casts = [
        pl.col(name).cast(dtype, strict=False).alias(name)
        for name, dtype in schema.items()
        if name in df.columns and df.schema[name] != dtype
    ]
    if casts:
        df = df.with_columns(casts)
    return df


def _with_dt(df: pl.DataFrame, source: str, target: str) -> pl.DataFrame:
    return df.with_columns(
        pl.from_epoch(pl.col(source), time_unit="ms")
        .dt.replace_time_zone("UTC")
        .alias(target)
    )


def _concat(frames: Iterable[pl.DataFrame], schema: Mapping[str, pl.DataType]) -> pl.DataFrame:
    frames = [frame for frame in frames if frame is not None and not frame.is_empty()]
    if not frames:
        return _empty(schema)
    return pl.concat(frames, how="vertical_relaxed").select(list(schema))


# ---------------------------------------------------------------------------
# Run discovery
# ---------------------------------------------------------------------------
def list_runs(root: str | Path | None = None, home: str | Path | None = None) -> pl.DataFrame:
    """Discover run directories.

    A directory is considered a run when it contains metadata.json, metrics.json
    or any of the parquet artifacts. Runs without trades.parquet remain visible
    but are marked ``has_trades == False`` so the UI can explain why they cannot
    be replayed.
    """
    base = runs_dir(root, home)
    if not base.exists() or not base.is_dir():
        return _empty(RUN_SCHEMA)

    rows: list[dict[str, Any]] = []
    for path in sorted(base.iterdir()):
        if not path.is_dir():
            continue
        metadata = _read_json(path / "metadata.json")
        metrics = _read_json(path / "metrics.json")
        artifact_names = {
            "has_trades": path.joinpath("trades.parquet").exists(),
            "has_signals": path.joinpath("signals.parquet").exists(),
            "has_fills": path.joinpath("fills.parquet").exists(),
            "has_equity": path.joinpath("equity.parquet").exists(),
        }
        if not (metadata or metrics or any(artifact_names.values())):
            continue

        trades_df = _read_parquet(path / "trades.parquet", TRADE_SCHEMA)
        config = metadata.get("config") if isinstance(metadata.get("config"), dict) else {}
        versions = (
            trades_df.get_column("strategy_version").drop_nulls().unique().to_list()
            if "strategy_version" in trades_df.columns and not trades_df.is_empty()
            else []
        )
        n_trades = metrics.get("trades")
        if n_trades is None:
            n_trades = trades_df.height
        net_pnl = metrics.get("net_pnl")
        if net_pnl is None and not trades_df.is_empty():
            net_pnl = trades_df.get_column("net_pnl").sum()
        return_pct = metrics.get("return_pct")

        source = metadata.get("source") or config.get("source") or "backtest"
        rows.append(
            {
                "run_id": path.name,
                "path": str(path),
                "source": str(source),
                "strategy": str(
                    metadata.get("strategy") or config.get("strategy") or _strategy_from_name(path.name)
                ),
                "strategy_version": str(versions[0]) if versions else None,
                "created": str(metadata.get("created") or ""),
                "trades": int(n_trades or 0),
                **{key: float(value) if value is not None else None for key, value in {
                    "net_pnl": net_pnl,
                    "return_pct": return_pct,
                }.items()},
                **artifact_names,
            }
        )

    if not rows:
        return _empty(RUN_SCHEMA)
    return pl.DataFrame(rows, schema=RUN_SCHEMA).sort(["created", "run_id"], descending=[True, True])


def _strategy_from_name(run_id: str) -> str:
    parts = run_id.split("_")
    return parts[1] if len(parts) > 1 else "unknown"


# ---------------------------------------------------------------------------
# Artifact loaders
# ---------------------------------------------------------------------------
def load_metrics(run: str | Path) -> dict[str, Any]:
    return _read_json(Path(run) / "metrics.json")


def load_metadata(run: str | Path) -> dict[str, Any]:
    return _read_json(Path(run) / "metadata.json")


def load_trades(run: str | Path) -> pl.DataFrame:
    df = _read_parquet(Path(run) / "trades.parquet", TRADE_SCHEMA)
    if df.is_empty():
        return _empty({**TRADE_SCHEMA, **_trade_derived_schema()})
    df = (
        df.with_columns(
            pl.when(pl.col("side") == "LONG")
            .then(1)
            .when(pl.col("side") == "SHORT")
            .then(-1)
            .otherwise(0)
            .alias("side_sign"),
            (pl.col("net_pnl") > 0).alias("win"),
        )
        .with_columns(
            (pl.col("exit_ts_ms") - pl.col("entry_ts_ms")).alias("duration_ms"),
        )
    )
    df = _with_dt(df, "entry_ts_ms", "entry_dt")
    df = _with_dt(df, "exit_ts_ms", "exit_dt")
    return df.with_columns(
        pl.col("entry_dt").dt.hour().alias("entry_hour"),
        (pl.col("duration_ms") / 60_000.0).alias("duration_min"),
    )


def _trade_derived_schema() -> dict[str, pl.DataType]:
    return {
        "side_sign": pl.Int64,
        "win": pl.Boolean,
        "duration_ms": pl.Int64,
        "entry_dt": pl.Datetime(time_unit="us", time_zone="UTC"),
        "exit_dt": pl.Datetime(time_unit="us", time_zone="UTC"),
        "entry_hour": pl.Int64,
        "duration_min": pl.Float64,
    }


def _ensure_trade_columns(df: pl.DataFrame) -> pl.DataFrame:
    """Add derived trade columns used by analytics when absent."""
    additions: list[pl.Expr] = []
    if "win" not in df.columns:
        additions.append((pl.col("net_pnl") > 0).alias("win"))
    if "side_sign" not in df.columns and "side" in df.columns:
        additions.append(
            pl.when(pl.col("side") == "LONG")
            .then(1)
            .when(pl.col("side") == "SHORT")
            .then(-1)
            .otherwise(0)
            .alias("side_sign")
        )
    return df.with_columns(additions) if additions else df


def load_equity(run: str | Path, starting_balance: float | None = None) -> pl.DataFrame:
    df = _read_parquet(Path(run) / "equity.parquet", EQUITY_SCHEMA)
    if df.is_empty():
        return _empty({**EQUITY_SCHEMA, **_equity_derived_schema()})
    df = df.unique(subset=["ts_ms"], keep="last").sort("ts_ms")
    df = _with_dt(df, "ts_ms", "dt")
    return compute_drawdown(df, starting_balance=starting_balance)


def _equity_derived_schema() -> dict[str, pl.DataType]:
    return {
        "dt": pl.Datetime(time_unit="us", time_zone="UTC"),
        "peak_equity": pl.Float64,
        "drawdown_usdt": pl.Float64,
        "drawdown_pct": pl.Float64,
        "cum_return_pct": pl.Float64,
        "ret_pct": pl.Float64,
    }


def compute_drawdown(equity: pl.DataFrame, starting_balance: float | None = None) -> pl.DataFrame:
    """Add peak, drawdown and return columns to an equity frame.

    ``drawdown_pct`` and ``cum_return_pct`` are fractions (0.17 == 17 percent),
    matching the units used by metrics.json. The series is sorted by ts_ms.
    """
    if equity.is_empty() or "equity" not in equity.columns:
        return _empty({**EQUITY_SCHEMA, **_equity_derived_schema()})
    df = equity.sort("ts_ms")
    initial = starting_balance if starting_balance is not None else float(df["equity"][0])
    df = df.with_columns(
        pl.max_horizontal(pl.col("equity").cum_max(), pl.lit(initial)).alias("peak_equity")
    ).with_columns(
        (pl.col("equity") - pl.col("peak_equity")).alias("drawdown_usdt"),
        (
            (pl.col("equity") - pl.col("peak_equity")) / pl.col("peak_equity")
        ).alias("drawdown_pct"),
        (pl.col("equity") / initial - 1.0).alias("cum_return_pct"),
        (pl.col("equity") / pl.col("equity").shift(1) - 1.0).alias("ret_pct"),
    )
    return df


def load_fills(run: str | Path) -> pl.DataFrame:
    df = _read_parquet(Path(run) / "fills.parquet", FILL_SCHEMA)
    if df.is_empty():
        return df
    return df.unique().sort("ts_ms")


def load_orders(run: str | Path) -> pl.DataFrame:
    df = _read_parquet(Path(run) / "orders.parquet", ORDER_SCHEMA)
    if df.is_empty():
        return df
    return normalize_orders(df).sort("ts_ms")


def normalize_orders(orders: pl.DataFrame) -> pl.DataFrame:
    """Normalize order kind.

    Some exporters swap ``kind`` and ``order_type`` (kind=MARKET,
    order_type=ENTRY). Prefer a known semantic kind from either column.
    """
    if orders.is_empty():
        return _empty(ORDER_SCHEMA)
    kind = pl.col("kind").str.to_uppercase()
    order_type = pl.col("order_type").str.to_uppercase()
    return orders.with_columns(
        pl.when(kind.is_in(list(_KNOWN_ORDER_KINDS)))
        .then(kind)
        .when(order_type.is_in(list(_KNOWN_ORDER_KINDS)))
        .then(order_type)
        .otherwise(kind)
        .alias("kind")
    )


def load_signals(run: str | Path) -> pl.DataFrame:
    df = _read_parquet(Path(run) / "signals.parquet", SIGNAL_SCHEMA)
    if df.is_empty():
        return df
    return df.sort("ts_ms")


def load_tags(run: str | Path) -> pl.DataFrame:
    """Read an explicit tags table if the exporter provided one."""
    base = Path(run)
    for name in ("tags.parquet", "trade_tags.parquet"):
        path = base / name
        if path.exists():
            df = _read_parquet(path, TAG_SCHEMA)
            if not df.is_empty():
                return df.select(list(TAG_SCHEMA)).unique()
    return _empty(TAG_SCHEMA)


def load_sensitivity(run: str | Path) -> pl.DataFrame:
    """Optional stop-buffer sweep output. Returns empty when absent."""
    base = Path(run)
    for path in sorted(base.glob("*sensitivity*.parquet")):
        try:
            return pl.read_parquet(path)
        except Exception:
            continue
    metrics = load_metrics(base)
    sweep = metrics.get("stop_buffer_sensitivity")
    if isinstance(sweep, list) and sweep and isinstance(sweep[0], dict):
        return pl.DataFrame(sweep)
    return pl.DataFrame()


# ---------------------------------------------------------------------------
# Trades enrichment and setup lookup
# ---------------------------------------------------------------------------
def enrich_trades(trades: pl.DataFrame, signals: pl.DataFrame) -> pl.DataFrame:
    """Attach recorded ENTER-signal context (regime, score, levels, ATR).

    The exact signal_id is preferred. A backward as-of join on symbol is only a
    fallback and never uses a signal timestamp after the trade entry.
    """
    if trades.is_empty():
        return trades
    df = trades
    if signals.is_empty():
        return df.with_columns(
            pl.lit(None, dtype=pl.Utf8).alias("regime"),
            pl.lit(None, dtype=pl.Float64).alias("signal_score"),
            pl.lit(None, dtype=pl.Float64).alias("support"),
            pl.lit(None, dtype=pl.Float64).alias("resistance"),
            pl.lit(None, dtype=pl.Float64).alias("atr"),
            pl.lit(None, dtype=pl.Float64).alias("signal_close"),
        )

    enters = signals.filter(pl.col("action") == "ENTER").sort("ts_ms")
    if enters.is_empty():
        return df.with_columns(
            pl.lit(None, dtype=pl.Utf8).alias("regime"),
            pl.lit(None, dtype=pl.Float64).alias("signal_score"),
            pl.lit(None, dtype=pl.Float64).alias("support"),
            pl.lit(None, dtype=pl.Float64).alias("resistance"),
            pl.lit(None, dtype=pl.Float64).alias("atr"),
            pl.lit(None, dtype=pl.Float64).alias("signal_close"),
        )
    exact = (
        enters.unique(subset=["signal_id"], keep="last")
        .select(
            pl.col("signal_id"),
            pl.col("ts_ms").alias("signal_ts_ms"),
            pl.col("score").alias("signal_score"),
            pl.col("regime"),
            pl.col("support"),
            pl.col("resistance"),
            pl.col("features"),
        )
    )
    df = df.join(exact, on="signal_id", how="left")

    missing = df.filter(pl.col("signal_ts_ms").is_null() & pl.col("entry_ts_ms").is_not_null())
    if not missing.is_empty():
        prior = enters.select(
            pl.col("symbol"),
            pl.col("ts_ms").alias("prior_ts_ms"),
            pl.col("score").alias("prior_score"),
            pl.col("regime").alias("prior_regime"),
            pl.col("support").alias("prior_support"),
            pl.col("resistance").alias("prior_resistance"),
            pl.col("features").alias("prior_features"),
        ).sort("prior_ts_ms")
        filled = (
            missing.drop(
                ["signal_ts_ms", "signal_score", "regime", "support", "resistance", "features"]
            )
            .sort("entry_ts_ms")
            .join_asof(
                prior,
                left_on="entry_ts_ms",
                right_on="prior_ts_ms",
                by="symbol",
                strategy="backward",
            )
            .with_columns(
                pl.col("prior_ts_ms").alias("signal_ts_ms"),
                pl.col("prior_score").alias("signal_score"),
                pl.col("prior_regime").alias("regime"),
                pl.col("prior_support").alias("support"),
                pl.col("prior_resistance").alias("resistance"),
                pl.col("prior_features").alias("features"),
            )
            .drop(["prior_ts_ms", "prior_score", "prior_regime", "prior_support", "prior_resistance", "prior_features"])
        )
        df = pl.concat(
            [df.filter(pl.col("signal_ts_ms").is_not_null()), filled], how="diagonal_relaxed"
        )

    features = pl.col("features").cast(pl.Utf8, strict=False)
    return df.with_columns(
        features.str.json_path_match("$.atr").cast(pl.Float64, strict=False).alias("atr"),
        features.str.json_path_match("$.close").cast(pl.Float64, strict=False).alias("signal_close"),
        pl.coalesce("signal_score", "setup_score").alias("entry_score"),
    )


def setup_for_trade(
    signals: pl.DataFrame,
    *,
    signal_id: str | None,
    symbol: str | None,
    entry_ts_ms: int | None,
) -> pl.DataFrame:
    """Return the recorded setup for a trade, never using future information.

    Preference order:
    1. the ENTER signal with the matching signal_id at or before entry,
    2. the latest ENTER signal for the symbol at or before entry.
    """
    schema = {**SIGNAL_SCHEMA, "level_source": pl.Utf8, "age_ms": pl.Int64}
    if signals.is_empty() or entry_ts_ms is None:
        return _empty(schema)
    eligible = signals.filter(
        (pl.col("action") == "ENTER") & (pl.col("ts_ms") <= int(entry_ts_ms))
    )
    source = None
    row = _empty(schema)
    if signal_id:
        exact = eligible.filter(pl.col("signal_id") == signal_id)
        if not exact.is_empty():
            row = exact.sort("ts_ms").tail(1)
            source = "recorded_signal"
    if row.is_empty() and symbol:
        prior = eligible.filter(pl.col("symbol") == symbol)
        if not prior.is_empty():
            row = prior.sort("ts_ms").tail(1)
            source = "nearest_prior_signal"
    if row.is_empty():
        return _empty(schema)
    return row.with_columns(
        pl.lit(source).alias("level_source"),
        (pl.lit(int(entry_ts_ms)) - pl.col("ts_ms")).alias("age_ms"),
    ).select(list(schema))


def trade_levels(
    orders: pl.DataFrame,
    *,
    signal_id: str | None,
    symbol: str | None,
    entry_ts_ms: int | None,
) -> pl.DataFrame:
    """Stop/TP prices recorded by the entry order batch.

    orders.parquet has no signal_id, so levels are matched by symbol and order
    timestamps in a tight window around entry. Only orders placed at or after
    the trade entry are considered.
    """
    schema = {"kind": pl.Utf8, "price": pl.Float64, "qty": pl.Float64, "ts_ms": pl.Int64}
    if orders.is_empty() or entry_ts_ms is None or symbol is None:
        return _empty(schema)
    window = orders.filter(
        (pl.col("symbol") == symbol)
        & (pl.col("kind").is_in(["STOP", "TP1", "TP2"]))
        & (pl.col("ts_ms") >= int(entry_ts_ms) - 60_000)
        & (pl.col("ts_ms") <= int(entry_ts_ms) + 60_000)
    )
    if window.is_empty():
        window = orders.filter(
            (pl.col("symbol") == symbol)
            & (pl.col("kind").is_in(["STOP", "TP1", "TP2"]))
            & (pl.col("ts_ms") >= int(entry_ts_ms))
            & (pl.col("ts_ms") <= int(entry_ts_ms) + 300_000)
        )
    if window.is_empty():
        return _empty(schema)
    return (
        window.sort("ts_ms")
        .group_by("kind", maintain_order=True)
        .agg(pl.col("price").first(), pl.col("qty").first(), pl.col("ts_ms").first())
        .select(list(schema))
    )


def fills_for_trade(
    fills: pl.DataFrame,
    *,
    signal_id: str | None,
    symbol: str | None,
    entry_ts_ms: int | None,
    exit_ts_ms: int | None,
) -> pl.DataFrame:
    if fills.is_empty():
        return fills
    if signal_id:
        matched = fills.filter(pl.col("signal_id") == signal_id)
        if not matched.is_empty():
            return matched.sort("ts_ms")
    if symbol is None or entry_ts_ms is None:
        return _empty(FILL_SCHEMA)
    end = int(exit_ts_ms) if exit_ts_ms is not None else int(entry_ts_ms) + 86_400_000
    return fills.filter(
        (pl.col("symbol") == symbol)
        & (pl.col("ts_ms") >= int(entry_ts_ms))
        & (pl.col("ts_ms") <= end)
    ).sort("ts_ms")


# ---------------------------------------------------------------------------
# Analytics
# ---------------------------------------------------------------------------
def derive_error_tags(trades: pl.DataFrame) -> pl.DataFrame:
    """Derive per-trade error tags when no explicit tags table exists.

    Rules are deliberately simple and inspectable:
    - exit_reason STOP -> stop_out, BREAKOUT -> breakout_against
    - exit_reason STOP_BREAKEVEN -> breakeven_stop
    - mae_r <= -1.0 -> full_stop_risk
    - mfe_r >= 1.0 and net_r < 0 -> gave_back_profit
    - mfe_r < 0.1 and net_r < 0 -> immediate_adverse
    - negative funding >= 10 percent of risk_usdt -> funding_drag
    - fees >= 10 percent of risk_usdt -> fee_drag
    - net_r <= -0.9 -> large_loss
    """
    if trades.is_empty():
        return _empty(TAG_SCHEMA)

    risk = pl.col("risk_usdt").abs()
    rules: list[tuple[str, pl.Expr]] = [
        ("stop_out", pl.col("exit_reason") == "STOP"),
        ("breakout_against", pl.col("exit_reason") == "BREAKOUT"),
        ("breakeven_stop", pl.col("exit_reason") == "STOP_BREAKEVEN"),
        ("full_stop_risk", pl.col("mae_r") <= -1.0),
        (
            "gave_back_profit",
            (pl.col("mfe_r") >= 1.0) & (pl.col("net_r") < 0),
        ),
        ("immediate_adverse", (pl.col("mfe_r") < 0.1) & (pl.col("net_r") < 0)),
        (
            "funding_drag",
            (pl.col("funding") < 0) & (pl.col("funding").abs() >= 0.10 * risk),
        ),
        ("fee_drag", pl.col("fees").abs() >= 0.10 * risk),
        ("large_loss", pl.col("net_r") <= -0.9),
    ]
    frames = [
        trades.filter(condition).select(
            pl.col("trade_id"), pl.lit(tag).alias("tag"), pl.lit("derived").alias("source")
        )
        for tag, condition in rules
    ]
    frames = [frame for frame in frames if not frame.is_empty()]
    if not frames:
        return _empty(TAG_SCHEMA)
    return (
        pl.concat(frames)
        .with_columns(pl.lit(None, dtype=pl.Utf8).alias("notes"))
        .unique(subset=["trade_id", "tag"])
        .sort(["trade_id", "tag"])
        .select(list(TAG_SCHEMA))
    )


def tag_summary(trades: pl.DataFrame, tags: pl.DataFrame | None = None) -> pl.DataFrame:
    """Aggregate tags by frequency and R impact."""
    schema = {
        "tag": pl.Utf8,
        "source": pl.Utf8,
        "frequency": pl.Int64,
        "trades": pl.Int64,
        "total_r": pl.Float64,
        "avg_r": pl.Float64,
        "total_pnl": pl.Float64,
        "win_rate": pl.Float64,
        "loss_r": pl.Float64,
        "improvement_r": pl.Float64,
        "share_pct": pl.Float64,
    }
    if trades.is_empty():
        return _empty(schema)
    trades = _ensure_trade_columns(trades)
    if tags is None or tags.is_empty():
        tags = derive_error_tags(trades)
    if tags.is_empty():
        return _empty(schema)

    joined = (
        tags.select(["trade_id", "tag", "source"])
        .unique(subset=["trade_id", "tag"])
        .join(
            trades.select(["trade_id", "net_r", "net_pnl", "mfe_r", "win"]),
            on="trade_id",
            how="inner",
        )
    )
    if joined.is_empty():
        return _empty(schema)

    summary = joined.group_by("tag").agg(
        pl.col("source").first(),
        pl.len().alias("frequency"),
        pl.col("trade_id").n_unique().alias("trades"),
        pl.col("net_r").sum().alias("total_r"),
        pl.col("net_r").mean().alias("avg_r"),
        pl.col("net_pnl").sum().alias("total_pnl"),
        pl.col("win").mean().alias("win_rate"),
        pl.col("net_r").clip(lower_bound=0.0).sum().alias("_gain"),
        (-pl.col("net_r").clip(upper_bound=0.0)).sum().alias("loss_r"),
        (pl.col("mfe_r") - pl.col("net_r")).clip(lower_bound=0.0).sum().alias("_giveback"),
    )
    total_rows = joined.height
    return (
        summary.with_columns(
            pl.when(pl.col("tag") == "gave_back_profit")
            .then(pl.col("_giveback"))
            .otherwise(pl.col("loss_r"))
            .alias("improvement_r"),
            (pl.col("frequency") / total_rows * 100.0).alias("share_pct"),
        )
        .drop(["_gain", "_giveback"])
        .sort("improvement_r", descending=True)
        .select(list(schema))
    )


def pareto_curve(summary: pl.DataFrame, metric: str = "improvement_r") -> pl.DataFrame:
    """Ordered tag impact with a cumulative share for a Pareto chart."""
    schema = {
        "tag": pl.Utf8,
        "value": pl.Float64,
        "frequency": pl.Int64,
        "cumulative_pct": pl.Float64,
        "cumulative_value": pl.Float64,
    }
    if summary.is_empty() or metric not in summary.columns:
        return _empty(schema)
    ordered = summary.sort(metric, descending=True)
    total = float(ordered[metric].sum())
    return ordered.select(
        pl.col("tag"),
        pl.col(metric).alias("value"),
        pl.col("frequency"),
        pl.col(metric).cum_sum().alias("cumulative_value"),
    ).with_columns(
        pl.when(total > 0)
        .then(pl.col("cumulative_value") / total * 100.0)
        .otherwise(0.0)
        .alias("cumulative_pct")
    ).select(list(schema))


def trades_for_tag(
    trades: pl.DataFrame, tags: pl.DataFrame, tag: str
) -> pl.DataFrame:
    """Trades carrying one tag."""
    if trades.is_empty() or tags.is_empty():
        return trades.head(0)
    ids = tags.filter(pl.col("tag") == tag).select("trade_id").unique()
    return trades.join(ids, on="trade_id", how="semi").sort("entry_ts_ms", descending=True)


def calibration_by_decile(trades: pl.DataFrame, n_bins: int = 10) -> pl.DataFrame:
    """Bucket trades into setup-score deciles and summarize net R."""
    schema = {
        "decile": pl.Int64,
        "n": pl.Int64,
        "score_min": pl.Float64,
        "score_max": pl.Float64,
        "mean_score": pl.Float64,
        "mean_net_r": pl.Float64,
        "median_net_r": pl.Float64,
        "std_net_r": pl.Float64,
        "se": pl.Float64,
        "ci_low": pl.Float64,
        "ci_high": pl.Float64,
        "win_rate": pl.Float64,
        "total_r": pl.Float64,
    }
    if trades.is_empty() or "setup_score" not in trades.columns:
        return _empty(schema)
    trades = _ensure_trade_columns(trades)
    df = trades.filter(
        pl.col("setup_score").is_not_null() & pl.col("net_r").is_not_null()
    )
    if df.is_empty():
        return _empty(schema)
    bins = max(1, min(int(n_bins), df.height))
    df = df.with_columns(
        (((pl.col("setup_score").rank(method="ordinal") - 1) * bins) // df.height + 1)
        .cast(pl.Int64)
        .alias("decile")
    )
    out = df.group_by("decile").agg(
        pl.len().alias("n"),
        pl.col("setup_score").min().alias("score_min"),
        pl.col("setup_score").max().alias("score_max"),
        pl.col("setup_score").mean().alias("mean_score"),
        pl.col("net_r").mean().alias("mean_net_r"),
        pl.col("net_r").median().alias("median_net_r"),
        pl.col("net_r").std().alias("std_net_r"),
        pl.col("win").mean().alias("win_rate"),
        pl.col("net_r").sum().alias("total_r"),
    )
    return (
        out.with_columns(
            (pl.col("std_net_r").fill_null(0.0) / pl.col("n").sqrt()).alias("se")
        )
        .with_columns(
            (pl.col("mean_net_r") - 1.96 * pl.col("se")).alias("ci_low"),
            (pl.col("mean_net_r") + 1.96 * pl.col("se")).alias("ci_high"),
        )
        .sort("decile")
        .select(list(schema))
    )


def score_trend(trades: pl.DataFrame) -> pl.DataFrame:
    """Least-squares slope of net R against setup score."""
    schema = {
        "n": pl.Int64,
        "slope": pl.Float64,
        "intercept": pl.Float64,
        "r2": pl.Float64,
        "x_min": pl.Float64,
        "x_max": pl.Float64,
    }
    if trades.is_empty():
        return _empty(schema)
    df = trades.filter(
        pl.col("setup_score").is_not_null() & pl.col("net_r").is_not_null()
    ).select("setup_score", "net_r")
    if df.height < 2:
        return _empty(schema)
    stats = df.select(
        pl.len().alias("n"),
        pl.col("setup_score").mean().alias("mx"),
        pl.col("net_r").mean().alias("my"),
        ((pl.col("setup_score") - pl.col("setup_score").mean()) * (pl.col("net_r") - pl.col("net_r").mean())).sum().alias("cov"),
        ((pl.col("setup_score") - pl.col("setup_score").mean()) ** 2).sum().alias("var_x"),
        (pl.col("setup_score").min()).alias("x_min"),
        (pl.col("setup_score").max()).alias("x_max"),
    ).row(0, named=True)
    if not stats["var_x"]:
        return _empty(schema)
    slope = stats["cov"] / stats["var_x"]
    intercept = stats["my"] - slope * stats["mx"]
    residuals = df.select(
        (
            (pl.col("net_r") - (slope * pl.col("setup_score") + intercept)) ** 2
        ).sum().alias("ss_res"),
        ((pl.col("net_r") - stats["my"]) ** 2).sum().alias("ss_tot"),
    ).row(0, named=True)
    r2 = None
    if residuals["ss_tot"]:
        r2 = 1.0 - residuals["ss_res"] / residuals["ss_tot"]
    return pl.DataFrame(
        [
            {
                "n": int(stats["n"]),
                "slope": float(slope),
                "intercept": float(intercept),
                "r2": float(r2) if r2 is not None else None,
                "x_min": float(stats["x_min"]),
                "x_max": float(stats["x_max"]),
            }
        ],
        schema=schema,
    )


def breakdown_by(trades: pl.DataFrame, dimension: str) -> pl.DataFrame:
    """Aggregate performance by a trade dimension.

    Supported: direction, symbol, regime, entry_hour, exit_reason.
    """
    mapping = {
        "direction": "side",
        "symbol": "symbol",
        "regime": "regime",
        "entry_hour": "entry_hour",
        "exit_reason": "exit_reason",
    }
    schema = {
        "key": pl.Utf8,
        "sort_key": pl.Int64,
        "trades": pl.Int64,
        "net_pnl": pl.Float64,
        "total_r": pl.Float64,
        "avg_r": pl.Float64,
        "median_r": pl.Float64,
        "win_rate": pl.Float64,
        "profit_factor": pl.Float64,
    }
    column = mapping.get(dimension)
    if column is None or trades.is_empty() or column not in trades.columns:
        return _empty(schema)
    trades = _ensure_trade_columns(trades)

    numeric = column == "entry_hour"
    df = trades.filter(pl.col(column).is_not_null())
    if df.is_empty():
        return _empty(schema)
    grouped = df.group_by(column).agg(
        pl.len().alias("trades"),
        pl.col("net_pnl").sum().alias("net_pnl"),
        pl.col("net_r").sum().alias("total_r"),
        pl.col("net_r").mean().alias("avg_r"),
        pl.col("net_r").median().alias("median_r"),
        pl.col("win").mean().alias("win_rate"),
        pl.col("net_r").clip(lower_bound=0.0).sum().alias("_gain"),
        (-pl.col("net_r").clip(upper_bound=0.0)).sum().alias("loss_abs"),
    )
    return (
        grouped.with_columns(
            pl.col(column).cast(pl.Utf8).alias("key"),
            (
                pl.when(pl.col("loss_abs") > 0)
                .then(pl.col("_gain") / pl.col("loss_abs"))
                .otherwise(None)
            ).alias("profit_factor"),
            (
                pl.col(column).cast(pl.Int64)
                if numeric
                else pl.lit(0, dtype=pl.Int64)
            ).alias("sort_key"),
        )
        .drop(["_gain", "loss_abs", column])
        .sort(["sort_key", "net_pnl"], descending=[False, True])
        .select(list(schema))
    )


def long_short_summary(trades: pl.DataFrame) -> pl.DataFrame:
    """Per-direction performance table."""
    schema = {
        "side": pl.Utf8,
        "trades": pl.Int64,
        "net_pnl": pl.Float64,
        "total_r": pl.Float64,
        "avg_r": pl.Float64,
        "win_rate": pl.Float64,
        "profit_factor": pl.Float64,
        "tp1_rate": pl.Float64,
        "stop_rate": pl.Float64,
        "funding": pl.Float64,
    }
    if trades.is_empty():
        return _empty(schema)
    trades = _ensure_trade_columns(trades)
    out = trades.filter(pl.col("side").is_not_null()).group_by("side").agg(
        pl.len().alias("trades"),
        pl.col("net_pnl").sum().alias("net_pnl"),
        pl.col("net_r").sum().alias("total_r"),
        pl.col("net_r").mean().alias("avg_r"),
        pl.col("win").mean().alias("win_rate"),
        pl.col("net_r").clip(lower_bound=0.0).sum().alias("_gain"),
        (-pl.col("net_r").clip(upper_bound=0.0)).sum().alias("loss_abs"),
        pl.col("tp1_filled").fill_null(False).mean().alias("tp1_rate"),
        (pl.col("exit_reason") == "STOP").mean().alias("stop_rate"),
        pl.col("funding").sum().alias("funding"),
    )
    return (
        out.with_columns(
            pl.when(pl.col("loss_abs") > 0)
            .then(pl.col("_gain") / pl.col("loss_abs"))
            .otherwise(None)
            .alias("profit_factor")
        )
        .drop(["_gain", "loss_abs"])
        .sort("net_pnl", descending=True)
        .select(list(schema))
    )


def breakdown_by_tag(trades: pl.DataFrame, tags: pl.DataFrame | None = None) -> pl.DataFrame:
    """Performance grouped by derived or explicit trade tag."""
    schema = {
        "key": pl.Utf8,
        "sort_key": pl.Int64,
        "trades": pl.Int64,
        "net_pnl": pl.Float64,
        "total_r": pl.Float64,
        "avg_r": pl.Float64,
        "median_r": pl.Float64,
        "win_rate": pl.Float64,
        "profit_factor": pl.Float64,
    }
    if trades.is_empty():
        return _empty(schema)
    trades = _ensure_trade_columns(trades)
    if tags is None or tags.is_empty():
        tags = derive_error_tags(trades)
    if tags.is_empty():
        return _empty(schema)
    joined = tags.select(["trade_id", "tag"]).unique().join(
        trades.select(["trade_id", "net_pnl", "net_r", "win"]), on="trade_id", how="inner"
    )
    if joined.is_empty():
        return _empty(schema)
    out = joined.group_by("tag").agg(
        pl.len().alias("trades"),
        pl.col("net_pnl").sum(),
        pl.col("net_r").sum().alias("total_r"),
        pl.col("net_r").mean().alias("avg_r"),
        pl.col("net_r").median().alias("median_r"),
        pl.col("win").mean().alias("win_rate"),
        pl.col("net_r").clip(lower_bound=0.0).sum().alias("_gain"),
        (-pl.col("net_r").clip(upper_bound=0.0)).sum().alias("loss_abs"),
    )
    return (
        out.with_columns(
            pl.col("tag").alias("key"),
            pl.lit(0, dtype=pl.Int64).alias("sort_key"),
            pl.when(pl.col("loss_abs") > 0)
            .then(pl.col("_gain") / pl.col("loss_abs"))
            .otherwise(None)
            .alias("profit_factor"),
        )
        .drop(["_gain", "loss_abs", "tag"])
        .sort("total_r")
        .select(list(schema))
    )


def trade_returns(trades: pl.DataFrame) -> pl.DataFrame:
    """Trade-ordered return series for the overview chart."""
    schema = {
        "entry_dt": pl.Datetime(time_unit="us", time_zone="UTC"),
        "net_r": pl.Float64,
        "net_pnl": pl.Float64,
        "cumulative_r": pl.Float64,
        "cumulative_pnl": pl.Float64,
    }
    if trades.is_empty():
        return _empty(schema)
    df = trades.sort("entry_ts_ms")
    return df.select(
        pl.col("entry_dt"),
        pl.col("net_r"),
        pl.col("net_pnl"),
        pl.col("net_r").cum_sum().alias("cumulative_r"),
        pl.col("net_pnl").cum_sum().alias("cumulative_pnl"),
    )


# ---------------------------------------------------------------------------
# Execution quality
# ---------------------------------------------------------------------------
def execution_by_trade(trades: pl.DataFrame, fills: pl.DataFrame) -> pl.DataFrame:
    """Compare expected entry price with the first ENTRY fill price."""
    schema = {
        **TRADE_SCHEMA,
        "actual_entry_price": pl.Float64,
        "actual_entry_ts_ms": pl.Int64,
        "entry_fee": pl.Float64,
        "entry_liquidity": pl.Utf8,
        "slippage_bps": pl.Float64,
        "slippage_usdt": pl.Float64,
        "has_fill": pl.Boolean,
    }
    if trades.is_empty():
        return _empty(schema)
    trades = _ensure_trade_columns(trades)
    if fills.is_empty():
        return trades.with_columns(
            pl.lit(None, dtype=pl.Float64).alias("actual_entry_price"),
            pl.lit(None, dtype=pl.Int64).alias("actual_entry_ts_ms"),
            pl.lit(None, dtype=pl.Float64).alias("entry_fee"),
            pl.lit(None, dtype=pl.Utf8).alias("entry_liquidity"),
            pl.lit(None, dtype=pl.Float64).alias("slippage_bps"),
            pl.lit(None, dtype=pl.Float64).alias("slippage_usdt"),
            pl.lit(False).alias("has_fill"),
        ).select(list(schema))

    entries = (
        fills.filter(pl.col("kind") == "ENTRY")
        .sort("ts_ms")
        .group_by("signal_id")
        .agg(
            pl.col("price").first().alias("actual_entry_price"),
            pl.col("ts_ms").first().alias("actual_entry_ts_ms"),
            pl.col("fee").first().alias("entry_fee"),
            pl.col("liquidity").first().alias("entry_liquidity"),
        )
    )
    out = trades.join(entries, on="signal_id", how="left").with_columns(
        pl.when(pl.col("actual_entry_price").is_not_null())
        .then(
            (pl.col("actual_entry_price") - pl.col("entry_price"))
            / pl.col("entry_price")
            * 10_000.0
            * pl.col("side_sign")
        )
        .otherwise(None)
        .alias("slippage_bps"),
        pl.when(pl.col("actual_entry_price").is_not_null())
        .then(
            (pl.col("actual_entry_price") - pl.col("entry_price"))
            * pl.col("quantity")
            * pl.col("side_sign")
        )
        .otherwise(None)
        .alias("slippage_usdt"),
        pl.col("actual_entry_price").is_not_null().alias("has_fill"),
    )
    return out.select(list(schema))


def execution_summary(execution: pl.DataFrame) -> pl.DataFrame:
    schema = {
        "metric": pl.Utf8,
        "value": pl.Float64,
        "note": pl.Utf8,
    }
    if execution.is_empty():
        return _empty(schema)
    filled = execution.filter(pl.col("has_fill"))
    if filled.is_empty():
        return _empty(schema)
    abs_bps = filled.get_column("slippage_bps").abs()
    rows = [
        ("trades_with_fill", float(filled.height), "trades matched to an ENTRY fill"),
        ("mean_slippage_bps", float(filled["slippage_bps"].mean()), "positive is adverse"),
        ("median_slippage_bps", float(filled["slippage_bps"].median()), "positive is adverse"),
        ("p95_abs_slippage_bps", float(abs_bps.quantile(0.95, "nearest") or 0.0), "absolute"),
        (
            "pct_adverse_fills",
            float((filled["slippage_bps"] > 0).mean() * 100.0),
            "percent of entries filled worse than expected",
        ),
        ("total_slippage_usdt", float(filled["slippage_usdt"].sum()), "signed cost"),
        ("total_entry_fees", float(filled["entry_fee"].sum()), "USDT"),
    ]
    return pl.DataFrame(rows, schema=schema, orient="row")


def execution_by_symbol(execution: pl.DataFrame) -> pl.DataFrame:
    schema = {
        "symbol": pl.Utf8,
        "trades": pl.Int64,
        "mean_slippage_bps": pl.Float64,
        "total_slippage_usdt": pl.Float64,
        "mean_entry_fee": pl.Float64,
    }
    if execution.is_empty():
        return _empty(schema)
    filled = execution.filter(pl.col("has_fill"))
    if filled.is_empty():
        return _empty(schema)
    return filled.group_by("symbol").agg(
        pl.len().alias("trades"),
        pl.col("slippage_bps").mean().alias("mean_slippage_bps"),
        pl.col("slippage_usdt").sum().alias("total_slippage_usdt"),
        pl.col("entry_fee").mean().alias("mean_entry_fee"),
    ).sort("total_slippage_usdt", descending=True).select(list(schema))


def liquidity_mix(fills: pl.DataFrame) -> pl.DataFrame:
    schema = {
        "liquidity": pl.Utf8,
        "fills": pl.Int64,
        "qty": pl.Float64,
        "notional": pl.Float64,
        "fee": pl.Float64,
        "share_pct": pl.Float64,
    }
    if fills.is_empty():
        return _empty(schema)
    df = fills.with_columns(
        pl.col("liquidity").fill_null("UNKNOWN").str.to_uppercase(),
        (pl.col("price") * pl.col("qty")).alias("notional"),
    )
    out = df.group_by("liquidity").agg(
        pl.len().alias("fills"),
        pl.col("qty").sum(),
        pl.col("notional").sum(),
        pl.col("fee").sum(),
    )
    total = out["fills"].sum()
    return out.with_columns(
        (pl.col("fills") / total * 100.0).alias("share_pct")
    ).sort("fills", descending=True).select(list(schema))


def fee_breakdown(fills: pl.DataFrame) -> pl.DataFrame:
    schema = {
        "kind": pl.Utf8,
        "liquidity": pl.Utf8,
        "fills": pl.Int64,
        "notional": pl.Float64,
        "fee": pl.Float64,
        "fee_bps": pl.Float64,
    }
    if fills.is_empty():
        return _empty(schema)
    df = fills.with_columns(
        pl.col("liquidity").fill_null("UNKNOWN").str.to_uppercase(),
        (pl.col("price") * pl.col("qty")).alias("notional"),
    )
    return (
        df.group_by(["kind", "liquidity"])
        .agg(
            pl.len().alias("fills"),
            pl.col("notional").sum(),
            pl.col("fee").sum(),
        )
        .with_columns(
            pl.when(pl.col("notional") > 0)
            .then(pl.col("fee") / pl.col("notional") * 10_000.0)
            .otherwise(None)
            .alias("fee_bps")
        )
        .sort("fee", descending=True)
        .select(list(schema))
    )


def order_health(
    metadata: Mapping[str, Any],
    orders: pl.DataFrame,
    fills: pl.DataFrame,
    signals: pl.DataFrame,
) -> pl.DataFrame:
    schema = {"metric": pl.Utf8, "value": pl.Float64, "note": pl.Utf8}
    rows: list[tuple[str, float | None, str]] = []
    rows.append(("orders_submitted", float(orders.height), "rows in orders.parquet"))
    rows.append(("fills_recorded", float(fills.height), "rows in fills.parquet"))
    if orders.height:
        rows.append(
            (
                "fill_rate_pct",
                fills.height / orders.height * 100.0,
                "fills divided by submitted orders",
            )
        )
    reject_counts = metadata.get("reject_counts")
    if isinstance(reject_counts, dict) and reject_counts:
        rows.append(
            (
                "entry_rejections",
                float(sum(int(v) for v in reject_counts.values() if isinstance(v, (int, float)))),
                "sum of metadata reject_counts",
            )
        )
    if not signals.is_empty():
        rejected = signals.filter(pl.col("action") == "REJECT").height
        rows.append(("rejected_signals", float(rejected), "signals with action REJECT"))
    denied = metadata.get("denied_orders")
    if denied is None:
        denied = metadata.get("rejected_orders")
    rows.append(
        (
            "denied_orders",
            float(denied) if isinstance(denied, (int, float)) else None,
            "not exported by the current backtest adapter" if denied is None else "from metadata",
        )
    )
    config = metadata.get("config") if isinstance(metadata.get("config"), dict) else {}
    latency = config.get("latency_ms")
    rows.append(
        (
            "latency_ms",
            float(latency) if isinstance(latency, (int, float)) else None,
            "configured latency; 0 means a zero-latency simulation"
            if latency is not None
            else "not recorded",
        )
    )
    return pl.DataFrame(rows, schema=schema, orient="row").with_columns(
        pl.col("value").cast(pl.Float64)
    )


def reject_counts_table(metadata: Mapping[str, Any]) -> pl.DataFrame:
    """Metadata reject_counts as a sorted frame."""
    schema = {"reason": pl.Utf8, "count": pl.Int64, "share_pct": pl.Float64}
    counts = metadata.get("reject_counts")
    if not isinstance(counts, dict) or not counts:
        return _empty(schema)
    rows = [
        {"reason": str(reason), "count": int(value)}
        for reason, value in counts.items()
        if isinstance(value, (int, float))
    ]
    if not rows:
        return _empty(schema)
    df = pl.DataFrame(rows, schema={"reason": pl.Utf8, "count": pl.Int64})
    total = int(df["count"].sum())
    return df.with_columns(
        (pl.col("count") / total * 100.0).alias("share_pct")
    ).sort("count", descending=True).select(list(schema))


# ---------------------------------------------------------------------------
# Rejected signals and hypothetical outcomes
# ---------------------------------------------------------------------------
def rejected_summary(signals: pl.DataFrame) -> pl.DataFrame:
    schema = {
        "reject_reason": pl.Utf8,
        "count": pl.Int64,
        "share_pct": pl.Float64,
        "avg_score": pl.Float64,
        "p90_score": pl.Float64,
        "example_reason": pl.Utf8,
        "symbols": pl.Int64,
        "first_ts_ms": pl.Int64,
        "last_ts_ms": pl.Int64,
    }
    if signals.is_empty():
        return _empty(schema)
    rejected = signals.filter(pl.col("action") == "REJECT")
    if rejected.is_empty():
        return _empty(schema)
    total = rejected.height
    return (
        rejected.group_by(pl.col("reject_reason").fill_null("UNKNOWN"))
        .agg(
            pl.len().alias("count"),
            pl.col("score").mean().alias("avg_score"),
            pl.col("score").quantile(0.9, "nearest").alias("p90_score"),
            pl.col("reason").drop_nulls().first().alias("example_reason"),
            pl.col("symbol").n_unique().alias("symbols"),
            pl.col("ts_ms").min().alias("first_ts_ms"),
            pl.col("ts_ms").max().alias("last_ts_ms"),
        )
        .with_columns((pl.col("count") / total * 100.0).alias("share_pct"))
        .sort("count", descending=True)
        .select(list(schema))
    )


def infer_hypothetical_side(
    close: float | None, support: float | None, resistance: float | None
) -> str | None:
    """Infer the side a rejected signal would have taken from range location."""
    if close is None:
        return None
    if support is not None and resistance is not None and resistance > support:
        width = resistance - support
        if close <= support + 0.25 * width:
            return "LONG"
        if close >= resistance - 0.25 * width:
            return "SHORT"
        return None
    return None


def _feature_atr(signal: Mapping[str, Any]) -> float | None:
    value = signal.get("atr")
    if isinstance(value, (int, float)) and not math.isnan(value):
        return float(value)
    features = signal.get("features")
    payload: Any = features
    if isinstance(features, str):
        try:
            payload = json.loads(features)
        except json.JSONDecodeError:
            payload = None
    if isinstance(payload, Mapping):
        atr = payload.get("atr")
        if isinstance(atr, (int, float)):
            return float(atr)
    return None


def _feature_close(signal: Mapping[str, Any]) -> float | None:
    value = signal.get("close")
    if isinstance(value, (int, float)):
        return float(value)
    features = signal.get("features")
    payload: Any = features
    if isinstance(features, str):
        try:
            payload = json.loads(features)
        except json.JSONDecodeError:
            payload = None
    if isinstance(payload, Mapping):
        close = payload.get("close")
        if isinstance(close, (int, float)):
            return float(close)
    return None


def simulate_hypothetical(
    signal: Mapping[str, Any],
    bars: pl.DataFrame,
    *,
    rr_target: float = 2.0,
    buffer_atr: float = 0.35,
    horizon_bars: int = 240,
) -> pl.DataFrame:
    """Simulate a hypothetical +2R before -1R trade for one rejected signal.

    This is analysis, not a replay of an executed trade. The side is inferred
    from the recorded range location when the signal has no side (rejects are
    recorded before side selection). Stops use the recorded support/resistance
    plus an ATR buffer, falling back to ATR alone. If both bars and volatilty
    are missing the row is returned as invalid rather than fabricated.
    """
    row: dict[str, Any] = {
        "signal_ts_ms": int(signal.get("ts_ms") or 0),
        "symbol": signal.get("symbol"),
        "side": None,
        "entry_price": None,
        "stop_price": None,
        "target_price": None,
        "risk_per_unit": None,
        "outcome": "NO_DATA",
        "r_multiple": None,
        "bars_held": None,
        "mae_r": None,
        "mfe_r": None,
        "valid": False,
        "note": "",
    }
    ts = signal.get("ts_ms")
    if not isinstance(ts, (int, float)):
        row["note"] = "missing signal timestamp"
        return pl.DataFrame([row], schema=HYPOTHETICAL_SCHEMA)

    atr = _feature_atr(signal)
    support = signal.get("support")
    resistance = signal.get("resistance")
    support = float(support) if isinstance(support, (int, float)) else None
    resistance = float(resistance) if isinstance(resistance, (int, float)) else None
    close = _feature_close(signal)

    side = signal.get("side")
    if side not in ("LONG", "SHORT"):
        side = infer_hypothetical_side(close, support, resistance)
    row["side"] = side
    if side is None:
        row["outcome"] = "NO_SIDE"
        row["note"] = "no recorded side and price was not near a range edge"
        return pl.DataFrame([row], schema=HYPOTHETICAL_SCHEMA)

    path = (
        bars.filter(pl.col("ts_open_ms") >= int(ts)).sort("ts_open_ms").head(int(horizon_bars) + 1)
        if not bars.is_empty()
        else bars
    )
    if path.is_empty():
        row["note"] = "no market bars for this symbol and window"
        return pl.DataFrame([row], schema=HYPOTHETICAL_SCHEMA)

    first = path.row(0, named=True)
    entry = float(first["open"])
    if not math.isfinite(entry) or entry <= 0:
        row["note"] = "invalid entry bar"
        return pl.DataFrame([row], schema=HYPOTHETICAL_SCHEMA)

    if side == "LONG":
        boundary = support if support is not None and support < entry else None
        if boundary is not None:
            stop = boundary - (buffer_atr * atr if atr else 0.0015 * entry)
        elif atr:
            stop = entry - atr
        else:
            row["note"] = "no support level or ATR to size a stop"
            return pl.DataFrame([row], schema=HYPOTHETICAL_SCHEMA)
        risk = entry - stop
        target = entry + rr_target * risk
    else:
        boundary = resistance if resistance is not None and resistance > entry else None
        if boundary is not None:
            stop = boundary + (buffer_atr * atr if atr else 0.0015 * entry)
        elif atr:
            stop = entry + atr
        else:
            row["note"] = "no resistance level or ATR to size a stop"
            return pl.DataFrame([row], schema=HYPOTHETICAL_SCHEMA)
        risk = stop - entry
        target = entry - rr_target * risk

    if risk <= 0 or not math.isfinite(risk):
        row["note"] = "stop would be on the wrong side of entry"
        return pl.DataFrame([row], schema=HYPOTHETICAL_SCHEMA)

    mae = 0.0
    mfe = 0.0
    outcome = "OPEN"
    bars_held = 0
    last_close = entry
    for index, bar in enumerate(path.iter_rows(named=True), start=1):
        high = float(bar["high"])
        low = float(bar["low"])
        last_close = float(bar["close"])
        if side == "LONG":
            mae = min(mae, (low - entry) / risk)
            mfe = max(mfe, (high - entry) / risk)
            if low <= stop:
                outcome = "STOP"
                bars_held = index
                break
            if high >= target:
                outcome = "TARGET"
                bars_held = index
                break
        else:
            mae = min(mae, (entry - high) / risk)
            mfe = max(mfe, (entry - low) / risk)
            if high >= stop:
                outcome = "STOP"
                bars_held = index
                break
            if low <= target:
                outcome = "TARGET"
                bars_held = index
                break
    else:
        bars_held = path.height

    if outcome == "TARGET":
        r_multiple = rr_target
    elif outcome == "STOP":
        r_multiple = -1.0
    else:
        direction = 1.0 if side == "LONG" else -1.0
        r_multiple = (last_close - entry) / risk * direction

    row.update(
        {
            "entry_price": entry,
            "stop_price": stop,
            "target_price": target,
            "risk_per_unit": risk,
            "outcome": outcome,
            "r_multiple": r_multiple,
            "bars_held": bars_held,
            "mae_r": mae,
            "mfe_r": mfe,
            "valid": True,
            "note": "hypothetical, no fees, funding or slippage",
        }
    )
    return pl.DataFrame([row], schema=HYPOTHETICAL_SCHEMA)


def hypothetical_outcomes(
    signals: pl.DataFrame,
    bars_by_symbol: Mapping[str, pl.DataFrame],
    *,
    bars_by_ts: Mapping[Any, pl.DataFrame] | None = None,
    max_signals: int = 25,
    rr_target: float = 2.0,
    horizon_bars: int = 240,
) -> pl.DataFrame:
    """Run simulate_hypothetical for the highest-scoring rejected signals."""
    if signals.is_empty():
        return _empty(HYPOTHETICAL_SCHEMA)
    candidates = hypothetical_candidates(signals, max_signals=max_signals)
    if candidates.is_empty():
        return _empty(HYPOTHETICAL_SCHEMA)
    frames: list[pl.DataFrame] = []
    for row in candidates.iter_rows(named=True):
        bars = None
        if bars_by_ts is not None:
            key = (int(row.get("ts_ms") or 0), row.get("symbol"))
            bars = bars_by_ts.get(key)
            if bars is None:
                bars = bars_by_ts.get(int(row.get("ts_ms") or 0))
        if bars is None:
            bars = bars_by_symbol.get(row.get("symbol") or "", _empty(BAR_SCHEMA))
        frames.append(simulate_hypothetical(row, bars, rr_target=rr_target, horizon_bars=horizon_bars))
    return pl.concat(frames, how="vertical").select(list(HYPOTHETICAL_SCHEMA))


def hypothetical_candidates(signals: pl.DataFrame, *, max_signals: int = 25) -> pl.DataFrame:
    """Choose rejected signals that can actually be simulated.

    Signals with recorded support and resistance are preferred because they can
    be given an inferred side and a structural stop. If a run has none, the
    highest-scored rejects are returned and the simulator reports NO_SIDE.
    """
    if signals.is_empty():
        return signals
    rejected = signals.filter(pl.col("action") == "REJECT")
    if rejected.is_empty():
        return rejected
    with_levels = rejected.filter(
        pl.col("support").is_not_null()
        & pl.col("resistance").is_not_null()
        & pl.col("score").is_not_null()
    )
    near_edge = _empty(SIGNAL_SCHEMA)
    if not with_levels.is_empty():
        close = (
            with_levels["features"]
            .cast(pl.Utf8, strict=False)
            .str.json_path_match("$.close")
            .cast(pl.Float64, strict=False)
        )
        width = with_levels["resistance"] - with_levels["support"]
        near_edge = with_levels.filter(
            (close <= with_levels["support"] + 0.25 * width)
            | (close >= with_levels["resistance"] - 0.25 * width)
        )
    if not near_edge.is_empty():
        pool = near_edge
    elif not with_levels.is_empty():
        pool = with_levels
    else:
        pool = rejected
    return pool.sort(
        ["score", "ts_ms"], descending=[True, False], nulls_last=True
    ).head(int(max_signals))


def hypothetical_summary(results: pl.DataFrame) -> pl.DataFrame:
    schema = {
        "outcome": pl.Utf8,
        "count": pl.Int64,
        "share_pct": pl.Float64,
        "mean_r": pl.Float64,
        "total_r": pl.Float64,
    }
    if results.is_empty():
        return _empty(schema)
    total = results.height
    return (
        results.group_by("outcome")
        .agg(
            pl.len().alias("count"),
            pl.col("r_multiple").mean().alias("mean_r"),
            pl.col("r_multiple").sum().alias("total_r"),
        )
        .with_columns((pl.col("count") / total * 100.0).alias("share_pct"))
        .sort("count", descending=True)
        .select(list(schema))
    )


def hypothetical_with_context(results: pl.DataFrame, signals: pl.DataFrame) -> pl.DataFrame:
    schema = {
        "signal_ts_ms": pl.Int64,
        "symbol": pl.Utf8,
        "reject_reason": pl.Utf8,
        "score": pl.Float64,
        "side": pl.Utf8,
        "outcome": pl.Utf8,
        "r_multiple": pl.Float64,
        "bars_held": pl.Int64,
        "entry_price": pl.Float64,
        "stop_price": pl.Float64,
        "target_price": pl.Float64,
        "note": pl.Utf8,
    }
    if results.is_empty():
        return _empty(schema)
    context = signals.select(
        pl.col("ts_ms").alias("signal_ts_ms"),
        pl.col("symbol"),
        pl.col("reject_reason"),
        pl.col("score"),
    ).unique(subset=["signal_ts_ms", "symbol"], keep="last")
    return (
        results.join(context, on=["signal_ts_ms", "symbol"], how="left")
        .select(list(schema))
        .sort("signal_ts_ms", descending=True)
    )


# ---------------------------------------------------------------------------
# Market data
# ---------------------------------------------------------------------------
def _month_span(start_ms: int, end_ms: int) -> list[tuple[int, int]]:
    first = dt.datetime.fromtimestamp(start_ms / 1000.0, tz=dt.UTC)
    last = dt.datetime.fromtimestamp(end_ms / 1000.0, tz=dt.UTC)
    spans: list[tuple[int, int]] = []
    year, month = first.year, first.month
    while (year, month) <= (last.year, last.month):
        spans.append((year, month))
        month += 1
        if month == 13:
            year, month = year + 1, 1
    return spans


def bar_paths(
    symbol: str,
    start_ms: int,
    end_ms: int,
    home: str | Path | None = None,
) -> list[Path]:
    base = market_dir(home) / f"symbol={symbol.upper()}"
    paths: list[Path] = []
    for year, month in _month_span(int(start_ms), int(end_ms)):
        path = base / f"year={year}" / f"month={month:02d}" / "data.parquet"
        if path.exists():
            paths.append(path)
    if paths:
        return paths
    return sorted(base.glob("year=*/month=*/data.parquet"))


def load_bars(
    symbol: str,
    start_ms: int,
    end_ms: int,
    home: str | Path | None = None,
) -> pl.DataFrame:
    """Load 1m canonical bars for a symbol and timestamp window."""
    paths = bar_paths(symbol, start_ms, end_ms, home)
    if not paths:
        return _empty(BAR_SCHEMA)
    try:
        df = (
            pl.scan_parquet([str(path) for path in paths])
            .filter(
                (pl.col("ts_open_ms") >= int(start_ms))
                & (pl.col("ts_open_ms") <= int(end_ms))
            )
            .collect()
        )
    except Exception:
        return _empty(BAR_SCHEMA)
    if df.is_empty():
        return _empty(BAR_SCHEMA)
    additions = [
        pl.lit(None, dtype=dtype).alias(name)
        for name, dtype in BAR_SCHEMA.items()
        if name not in df.columns
    ]
    if additions:
        df = df.with_columns(additions)
    df = df.sort("ts_open_ms").unique(subset=["ts_open_ms"], keep="last")
    return _with_dt(df, "ts_open_ms", "dt")


def resample_bars(bars: pl.DataFrame, every: str = "15m") -> pl.DataFrame:
    """Resample 1m bars into a coarser interval (default 15m)."""
    schema = {
        "symbol": pl.Utf8,
        "dt": pl.Datetime(time_unit="us", time_zone="UTC"),
        "ts_open_ms": pl.Int64,
        "ts_close_ms": pl.Int64,
        "open": pl.Float64,
        "high": pl.Float64,
        "low": pl.Float64,
        "close": pl.Float64,
        "volume": pl.Float64,
    }
    if bars.is_empty():
        return _empty(schema)
    df = bars.with_columns(
        pl.from_epoch(pl.col("ts_open_ms"), time_unit="ms")
        .dt.replace_time_zone("UTC")
        .alias("dt")
    ).sort("dt")
    out = df.group_by_dynamic("dt", every=every, closed="left", label="left").agg(
        pl.col("symbol").first(),
        pl.col("ts_open_ms").first(),
        pl.col("ts_close_ms").last(),
        pl.col("open").first(),
        pl.col("high").max(),
        pl.col("low").min(),
        pl.col("close").last(),
        pl.col("volume").sum(),
    )
    return out.select(list(schema))
