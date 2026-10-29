"""Shared Streamlit helpers: theme, sidebar, formatting, page guards."""

from __future__ import annotations

from typing import Any

import plotly.graph_objects as go
import polars as pl
import streamlit as st

from dashboard import cache, data

# Dark, calm palette that also reads well on a light background.
COLORS = {
    "bg": "#0b0f14",
    "panel": "#121821",
    "grid": "#26303d",
    "text": "#e6edf3",
    "muted": "#8b949e",
    "accent": "#58a6ff",
    "green": "#3fb950",
    "red": "#f85149",
    "amber": "#d29922",
    "purple": "#bc8cff",
    "cyan": "#39c5cf",
    "long": "#3fb950",
    "short": "#f85149",
}

CSS = """
<style>
:root { color-scheme: dark; }
.stApp {
  background: radial-gradient(circle at 15% -10%, #16202c 0%, #0b0f14 45%, #080b0f 100%);
  color: #e6edf3;
}
[data-testid="stSidebar"] {
  background: linear-gradient(180deg, #0f151d 0%, #0b0f14 100%);
  border-right: 1px solid #1b2430;
}
[data-testid="stMetric"] {
  background: linear-gradient(180deg, #141c26 0%, #10161e 100%);
  border: 1px solid #212b38;
  border-radius: 10px;
  padding: 12px 14px;
}
[data-testid="stMetricLabel"] { color: #8b949e; }
[data-testid="stMetricValue"] { color: #e6edf3; font-variant-numeric: tabular-nums; }
h1, h2, h3 { letter-spacing: -0.01em; }
h1 { font-weight: 650; }
.block-container { padding-top: 2.0rem; padding-bottom: 3rem; max-width: 1500px; }
div[data-testid="stDataFrame"] { border: 1px solid #212b38; border-radius: 8px; }
.stTabs [data-baseweb="tab-list"] { gap: 4px; }
.stTabs [data-baseweb="tab"] {
  background: #121821;
  border: 1px solid #212b38;
  border-radius: 8px 8px 0 0;
  padding: 6px 14px;
}
.stTabs [aria-selected="true"] { color: #e6edf3; border-bottom-color: #58a6ff; }
hr { border-color: #212b38; }
.small-note { color: #8b949e; font-size: 0.82rem; line-height: 1.45; }
.tag-pill {
  display: inline-block; padding: 1px 8px; margin: 1px 4px 1px 0;
  border-radius: 999px; background: #1c2632; border: 1px solid #2a3544;
  color: #c9d1d9; font-size: 0.78rem;
}
</style>
"""


def configure_page() -> None:
    st.set_page_config(
        page_title="Range Lab",
        page_icon=":material/candlestick_chart:",
        layout="wide",
        initial_sidebar_state="expanded",
    )
    st.markdown(CSS, unsafe_allow_html=True)


def style_fig(fig: go.Figure, *, height: int = 380, showlegend: bool = True) -> go.Figure:
    fig.update_layout(
        template="plotly_dark",
        height=height,
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font={"color": COLORS["text"], "family": "Inter, system-ui, sans-serif", "size": 12},
        margin={"l": 8, "r": 8, "t": 42, "b": 8},
        showlegend=showlegend,
        legend={"orientation": "h", "yanchor": "bottom", "y": 1.02, "x": 0},
        hoverlabel={"bgcolor": "#161b22", "font": {"color": COLORS["text"]}},
        colorway=[
            COLORS["accent"],
            COLORS["green"],
            COLORS["red"],
            COLORS["amber"],
            COLORS["purple"],
            COLORS["cyan"],
        ],
    )
    fig.update_xaxes(gridcolor=COLORS["grid"], zerolinecolor=COLORS["grid"])
    fig.update_yaxes(gridcolor=COLORS["grid"], zerolinecolor=COLORS["grid"])
    return fig


def sidebar(runs: pl.DataFrame) -> None:
    """Render the shared source/strategy-version/run selector."""
    with st.sidebar:
        st.markdown("### Range Lab")
        st.caption("Backtest research dashboard")
        st.divider()
        if runs.is_empty():
            st.warning("No runs found.")
            st.caption(str(data.runs_dir()))
            st.session_state.pop("run_dir", None)
            return

        sources = sorted(runs["source"].drop_nulls().unique().to_list())
        if st.session_state.get("source_filter") not in ["All", *sources]:
            st.session_state.pop("source_filter", None)
        source = st.selectbox("Source", ["All", *sources], key="source_filter")
        source_df = runs if source == "All" else runs.filter(pl.col("source") == source)

        versions = sorted(source_df["strategy_version"].drop_nulls().unique().to_list())
        if st.session_state.get("version_filter") not in ["All", *versions]:
            st.session_state.pop("version_filter", None)
        version = st.selectbox("Strategy version", ["All", *versions], key="version_filter")
        version_df = (
            source_df if version == "All" else source_df.filter(pl.col("strategy_version") == version)
        )
        if version_df.is_empty():
            version_df = source_df

        options = version_df["run_id"].to_list()
        if st.session_state.get("run_id") not in options:
            st.session_state.pop("run_id", None)
        run_id = st.selectbox("Run", options, key="run_id", format_func=_run_label)
        selected = version_df.filter(pl.col("run_id") == run_id).row(0, named=True)
        st.session_state["run_dir"] = selected["path"]

        st.divider()
        st.caption(f"{len(options)} run(s) in selection")
        pnl = selected.get("net_pnl")
        ret = selected.get("return_pct")
        col1, col2 = st.columns(2)
        col1.metric("Trades", int(selected.get("trades") or 0))
        col2.metric("Net PnL", "n/a" if pnl is None else f"{pnl:,.2f}")
        if ret is not None:
            st.caption(f"Return {ret:,.2f}%")
        st.caption(f"`{run_id}`")


def _run_label(run_id: str) -> str:
    return run_id


def get_run() -> dict[str, Any] | None:
    path = st.session_state.get("run_dir")
    if path is None:
        runs = cache.list_runs(str(data.runs_dir()))
        if runs.is_empty():
            return None
        path = runs["path"][0]
        st.session_state["run_dir"] = path
    return cache.load_bundle(path)


def require_run() -> dict[str, Any] | None:
    bundle = get_run()
    if bundle is None:
        empty_state(
            "No runs found. Run a backtest first, or point `TRADEBOT_RUNS_DIR` at a runs directory."
        )
        return None
    return bundle


def require_trades(bundle: dict[str, Any] | None) -> bool:
    if bundle is None:
        return False
    if bundle["trades"].is_empty():
        empty_state(
            "This run has no trades.parquet. Runs produced before the trade exporter was added "
            "still appear in the selector, but cannot be analysed here."
        )
        return False
    return True


def empty_state(message: str, icon: str = "info") -> None:
    st.info(message, icon=f":material/{icon}:")


def run_header(bundle: dict[str, Any]) -> None:
    metadata = bundle["metadata"]
    created = metadata.get("created")
    strategy = metadata.get("config", {}).get("strategy") if isinstance(metadata.get("config"), dict) else None
    config_hash = metadata.get("config_hash")
    st.caption(
        " · ".join(
            part
            for part in [
                f"`{bundle['run_id']}`",
                f"strategy {strategy}" if strategy else None,
                f"config {config_hash}" if config_hash else None,
                str(created) if created else None,
            ]
            if part
        )
    )


def page_title(title: str, subtitle: str) -> None:
    st.title(title)
    st.markdown(f"<div class='small-note'>{subtitle}</div>", unsafe_allow_html=True)
    st.write("")


def fmt_pct(value: float | None, digits: int = 1, already_pct: bool = False) -> str:
    if value is None:
        return "n/a"
    scaled = value if already_pct else value * 100.0
    return f"{scaled:,.{digits}f}%"


def fmt_num(value: Any, digits: int = 2) -> str:
    if value is None:
        return "n/a"
    try:
        return f"{float(value):,.{digits}f}"
    except (TypeError, ValueError):
        return str(value)


def metric_row(items: list[tuple[str, str, str | None]]) -> None:
    """Render metrics in a row. Each item is (label, value, help)."""
    columns = st.columns(len(items))
    for column, (label, value, help_text) in zip(columns, items, strict=False):
        column.metric(label, value, help=help_text)


def table(df: pl.DataFrame, *, height: int | None = None, **kwargs: Any) -> None:
    st.dataframe(
        df.to_pandas() if not df.is_empty() else df,
        width="stretch",
        hide_index=True,
        height=height,
        **kwargs,
    )


def download_button(df: pl.DataFrame, label: str, filename: str) -> None:
    if df.is_empty():
        return
    st.download_button(
        label,
        data=df.write_csv(),
        file_name=filename,
        mime="text/csv",
        icon=":material/download:",
    )
