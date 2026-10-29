"""Streamlit caching wrappers around dashboard.data.

data.py stays importable without streamlit; this module is the only place that
depends on st.cache_data.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import polars as pl
import streamlit as st

from dashboard import data


@st.cache_data(show_spinner=False)
def list_runs(root: str) -> pl.DataFrame:
    return data.list_runs(root)


@st.cache_data(show_spinner=False)
def load_bundle(run_path: str) -> dict[str, Any]:
    path = Path(run_path)
    trades = data.load_trades(path)
    signals = data.load_signals(path)
    fills = data.load_fills(path)
    orders = data.load_orders(path)
    metrics = data.load_metrics(path)
    metadata = data.load_metadata(path)
    equity = data.load_equity(path, starting_balance=metrics.get("starting_balance"))
    tags = data.load_tags(path)
    if tags.is_empty() and not trades.is_empty():
        tags = data.derive_error_tags(trades)
    return {
        "run_id": path.name,
        "path": str(path),
        "trades": trades,
        "trades_enriched": data.enrich_trades(trades, signals),
        "signals": signals,
        "fills": fills,
        "orders": orders,
        "equity": equity,
        "metrics": metrics,
        "metadata": metadata,
        "tags": tags,
        "tag_summary": data.tag_summary(trades, tags),
        "sensitivity": data.load_sensitivity(path),
        "execution": data.execution_by_trade(trades, fills),
    }


@st.cache_data(show_spinner="Loading market bars")
def load_bars(symbol: str, start_ms: int, end_ms: int, home: str) -> pl.DataFrame:
    return data.load_bars(symbol, start_ms, end_ms, home)


@st.cache_data(show_spinner=False)
def resample_bars(bars: pl.DataFrame, every: str) -> pl.DataFrame:
    return data.resample_bars(bars, every)
