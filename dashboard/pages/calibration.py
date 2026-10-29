from __future__ import annotations

import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

from dashboard import common, data

common.page_title(
    "Calibration",
    "Does a higher setup score actually earn more R? Deciles, confidence bands and the score return scatter.",
)
bundle = common.require_run()
if bundle is None:
    st.stop()
common.run_header(bundle)
if not common.require_trades(bundle):
    st.stop()

trades = bundle["trades"]
if trades["setup_score"].drop_nulls().len() < 2:
    common.empty_state("Not enough scored trades to calibrate. Need at least two non-null setup scores.")
    st.stop()

max_bins = min(12, max(2, trades["setup_score"].drop_nulls().len()))
if max_bins <= 2:
    bins = max_bins
else:
    bins = st.slider("Score bins", min_value=2, max_value=max_bins, value=min(10, max_bins))
deciles = data.calibration_by_decile(trades, bins)
trend = data.score_trend(trades)
if deciles.is_empty():
    common.empty_state("Scored trades also need a non-null net R to calibrate.")
    st.stop()

common.metric_row(
    [
        ("Scored trades", f"{deciles['n'].sum():,}", "non-null score and net R"),
        ("Bins", str(deciles.height), "rank-based, equal count"),
        ("Mean R", f"{common.fmt_num(trades['net_r'].mean())} R", "all scored trades"),
        (
            "Score slope",
            f"{common.fmt_num(trend['slope'][0], 3)} R/score" if not trend.is_empty() else "n/a",
            "least squares",
        ),
        ("R squared", common.fmt_num(trend["r2"][0], 3) if not trend.is_empty() else "n/a", "fit"),
    ]
)
if not trend.is_empty() and trend["slope"][0] <= 0:
    st.warning("The fitted score slope is negative: higher scores did not earn more R in this run.")
elif not trend.is_empty():
    st.caption("A positive slope is necessary, not sufficient, evidence of a useful score.")

st.divider()
left, right = st.columns([1.1, 1.0])

with left:
    figure = make_subplots(specs=[[{"secondary_y": True}]])
    figure.add_trace(
        go.Bar(
            x=deciles["decile"].to_list(),
            y=deciles["n"].to_list(),
            name="trades",
            marker_color="rgba(88,166,255,0.22)",
        ),
        secondary_y=True,
    )
    figure.add_trace(
        go.Scatter(
            x=deciles["decile"].to_list(),
            y=deciles["ci_high"].to_list(),
            mode="lines",
            line={"width": 0},
            name="95% band",
            showlegend=True,
            hoverinfo="skip",
        ),
        secondary_y=False,
    )
    figure.add_trace(
        go.Scatter(
            x=deciles["decile"].to_list(),
            y=deciles["ci_low"].to_list(),
            mode="lines",
            line={"width": 0},
            fill="tonexty",
            fillcolor="rgba(188,140,255,0.18)",
            name="95% CI",
            hoverinfo="skip",
        ),
        secondary_y=False,
    )
    figure.add_trace(
        go.Scatter(
            x=deciles["decile"].to_list(),
            y=deciles["mean_net_r"].to_list(),
            mode="lines+markers",
            name="mean net R",
            line={"color": common.COLORS["purple"], "width": 2},
        ),
        secondary_y=False,
    )
    figure.add_trace(
        go.Scatter(
            x=deciles["decile"].to_list(),
            y=(deciles["win_rate"] * 100.0).to_list(),
            mode="lines+markers",
            name="win rate %",
            line={"color": common.COLORS["green"], "width": 1.4, "dash": "dot"},
        ),
        secondary_y=True,
    )
    figure.add_hline(y=0, line_dash="dot", line_color=common.COLORS["muted"], secondary_y=False)
    figure.update_layout(title="Mean net R and win rate by score bin")
    figure.update_xaxes(title_text="score bin (low to high)")
    figure.update_yaxes(title_text="net R", secondary_y=False)
    figure.update_yaxes(title_text="count / win %", secondary_y=True, showgrid=False)
    st.plotly_chart(
        common.style_fig(figure, height=450),
        width="stretch",
        config={"displayModeBar": False},
    )

with right:
    scatter = go.Figure()
    wins = trades.filter(trades["net_r"] > 0)
    losses = trades.filter(trades["net_r"] <= 0)
    scatter.add_trace(
        go.Scatter(
            x=wins["setup_score"].to_list(),
            y=wins["net_r"].to_list(),
            mode="markers",
            name="wins",
            marker={"color": common.COLORS["green"], "size": 7, "opacity": 0.75},
        )
    )
    scatter.add_trace(
        go.Scatter(
            x=losses["setup_score"].to_list(),
            y=losses["net_r"].to_list(),
            mode="markers",
            name="losses",
            marker={"color": common.COLORS["red"], "size": 7, "opacity": 0.75},
        )
    )
    if not trend.is_empty():
        x_min, x_max = float(trend["x_min"][0]), float(trend["x_max"][0])
        slope, intercept = float(trend["slope"][0]), float(trend["intercept"][0])
        scatter.add_trace(
            go.Scatter(
                x=[x_min, x_max],
                y=[intercept + slope * x_min, intercept + slope * x_max],
                mode="lines",
                name="linear fit",
                line={"color": common.COLORS["amber"], "width": 2},
            )
        )
    scatter.add_trace(
        go.Scatter(
            x=deciles["mean_score"].to_list(),
            y=deciles["ci_low"].to_list(),
            mode="lines",
            line={"width": 0},
            showlegend=False,
            hoverinfo="skip",
        )
    )
    scatter.add_trace(
        go.Scatter(
            x=deciles["mean_score"].to_list(),
            y=deciles["ci_high"].to_list(),
            mode="lines",
            line={"width": 0},
            fill="tonexty",
            fillcolor="rgba(88,166,255,0.14)",
            name="binned 95% CI",
            hoverinfo="skip",
        )
    )
    scatter.add_hline(y=0, line_dash="dot", line_color=common.COLORS["muted"])
    scatter.update_layout(title="Score versus net R", xaxis_title="setup score", yaxis_title="net R")
    st.plotly_chart(
        common.style_fig(scatter, height=450),
        width="stretch",
        config={"displayModeBar": False},
    )

st.divider()
st.subheader("Bin detail")
st.dataframe(
    deciles.to_pandas(),
    width="stretch",
    hide_index=True,
    column_config={
        "decile": "Bin",
        "n": st.column_config.NumberColumn("Trades", format="%d"),
        "score_min": st.column_config.NumberColumn("Score min", format="%.3f"),
        "score_max": st.column_config.NumberColumn("Score max", format="%.3f"),
        "mean_score": st.column_config.NumberColumn("Mean score", format="%.3f"),
        "mean_net_r": st.column_config.NumberColumn("Mean R", format="%.3f"),
        "median_net_r": st.column_config.NumberColumn("Median R", format="%.3f"),
        "std_net_r": st.column_config.NumberColumn("Std R", format="%.3f"),
        "ci_low": st.column_config.NumberColumn("CI low", format="%.3f"),
        "ci_high": st.column_config.NumberColumn("CI high", format="%.3f"),
        "win_rate": st.column_config.ProgressColumn(
            "Win rate", min_value=0.0, max_value=1.0, format="%.1f%%"
        ),
        "total_r": st.column_config.NumberColumn("Total R", format="%.2f"),
    },
)

st.divider()
st.subheader("Stop-buffer sensitivity")
sensitivity = bundle["sensitivity"]
if sensitivity.is_empty():
    config = bundle["metadata"].get("config") if isinstance(bundle["metadata"].get("config"), dict) else {}
    buffer_atr = (
        config.get("range_score", {}).get("stop_buffer_atr")
        if isinstance(config.get("range_score"), dict)
        else None
    )
    common.empty_state(
        "No stop-buffer sweep data in this run. The dashboard will chart it when a "
        "`*sensitivity*.parquet` artifact or `metrics.json.stop_buffer_sensitivity` is present."
    )
    if buffer_atr is not None:
        st.caption(f"Configured stop buffer: {buffer_atr} ATR. This is a single setting, not a sweep.")
else:
    st.dataframe(sensitivity.to_pandas(), width="stretch", hide_index=True)
