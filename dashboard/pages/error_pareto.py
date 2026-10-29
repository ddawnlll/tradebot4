from __future__ import annotations

import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

from dashboard import common, data

common.page_title(
    "Error Pareto",
    "Which failure modes cost the most R, how often they occur, and how much is plausibly recoverable.",
)
bundle = common.require_run()
if bundle is None:
    st.stop()
common.run_header(bundle)
if not common.require_trades(bundle):
    st.stop()

trades = bundle["trades"]
tags = bundle["tags"]
summary = bundle["tag_summary"]
if summary.is_empty():
    common.empty_state("No tags could be derived for this run.")
    st.stop()

pareto = data.pareto_curve(summary)
total_loss = float(summary["loss_r"].sum())
potential = float(summary["improvement_r"].sum())

common.metric_row(
    [
        ("Tags", str(summary.height), "derived or explicit"),
        ("Tagged trade rows", f"{int(summary['frequency'].sum()):,}", "a trade may carry several tags"),
        ("Total R loss", f"{common.fmt_num(total_loss)} R", "sum of losing R on tagged trades"),
        ("Improvement pool", f"{common.fmt_num(potential)} R", "recoverable loss estimate"),
        (
            "Top tag",
            summary["tag"][0],
            f"{summary['improvement_r'][0]:.2f} R potential",
        ),
    ]
)
if tags["source"].unique().to_list() == ["derived"]:
    st.caption(
        "No tags table exists in this run. Tags are derived from exit reason, MAE/MFE, "
        "negative funding and fees versus risk."
    )
else:
    st.caption("Using the explicit tags table exported with this run.")

st.divider()
figure = make_subplots(specs=[[{"secondary_y": True}]])
figure.add_trace(
    go.Bar(
        x=pareto["tag"].to_list(),
        y=pareto["value"].to_list(),
        name="improvement R",
        marker_color=common.COLORS["red"],
    ),
    secondary_y=False,
)
figure.add_trace(
    go.Scatter(
        x=pareto["tag"].to_list(),
        y=pareto["cumulative_pct"].to_list(),
        mode="lines+markers",
        name="cumulative %",
        line={"color": common.COLORS["amber"], "width": 2},
    ),
    secondary_y=True,
)
figure.update_layout(title="Pareto of estimated improvement potential")
figure.update_yaxes(title_text="R", secondary_y=False)
figure.update_yaxes(title_text="cumulative %", secondary_y=True, range=[0, 105], showgrid=False)
st.plotly_chart(
    common.style_fig(figure, height=420),
    use_container_width=True,
    config={"displayModeBar": False},
)

left, right = st.columns([1.0, 1.0])
with left:
    frequency = go.Figure(
        go.Bar(
            x=summary["tag"].to_list(),
            y=summary["frequency"].to_list(),
            marker_color=common.COLORS["accent"],
        )
    )
    frequency.update_layout(title="Tag frequency", yaxis_title="trade-tag rows")
    st.plotly_chart(
        common.style_fig(frequency, height=340, showlegend=False),
        use_container_width=True,
        config={"displayModeBar": False},
    )
with right:
    avg = go.Figure(
        go.Bar(
            x=summary["tag"].to_list(),
            y=summary["avg_r"].to_list(),
            marker_color=[
                common.COLORS["green"] if value >= 0 else common.COLORS["red"]
                for value in summary["avg_r"].to_list()
            ],
        )
    )
    avg.add_hline(y=0, line_dash="dot", line_color=common.COLORS["muted"])
    avg.update_layout(title="Average R per tag", yaxis_title="avg R")
    st.plotly_chart(
        common.style_fig(avg, height=340, showlegend=False),
        use_container_width=True,
        config={"displayModeBar": False},
    )

st.divider()
st.subheader("Tag table")
st.dataframe(
    summary.to_pandas(),
    use_container_width=True,
    hide_index=True,
    column_config={
        "tag": "Tag",
        "source": "Source",
        "frequency": st.column_config.NumberColumn("Rows", format="%d"),
        "trades": st.column_config.NumberColumn("Trades", format="%d"),
        "total_r": st.column_config.NumberColumn("Total R", format="%.2f"),
        "avg_r": st.column_config.NumberColumn("Avg R", format="%.3f"),
        "total_pnl": st.column_config.NumberColumn("Total PnL", format="%.2f"),
        "win_rate": st.column_config.ProgressColumn(
            "Win rate", min_value=0.0, max_value=1.0, format="%.1f%%"
        ),
        "loss_r": st.column_config.NumberColumn("Loss R", format="%.2f"),
        "improvement_r": st.column_config.NumberColumn("Potential R", format="%.2f"),
        "share_pct": st.column_config.NumberColumn("Share", format="%.1f%%"),
    },
)

st.divider()
st.subheader("Tag detail")
selected_tag = st.selectbox("Tag", summary["tag"].to_list())
detail = data.trades_for_tag(trades, tags, selected_tag)
if detail.is_empty():
    common.empty_state("No trades for this tag.")
else:
    st.dataframe(
        detail.select(
            "trade_id",
            "symbol",
            "side",
            "entry_dt",
            "exit_reason",
            "setup_score",
            "net_r",
            "mae_r",
            "mfe_r",
            "fees",
            "funding",
        ).to_pandas(),
        use_container_width=True,
        hide_index=True,
    )
    common.download_button(detail, "Download tagged trades CSV", f"{selected_tag}_trades.csv")
