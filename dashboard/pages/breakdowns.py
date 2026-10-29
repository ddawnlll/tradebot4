from __future__ import annotations

import plotly.graph_objects as go
import streamlit as st

from dashboard import charts, common, data

common.page_title(
    "Breakdowns",
    "Where the edge and the damage sit: direction, symbol, regime, entry hour, exit reason and error tags.",
)
bundle = common.require_run()
if bundle is None:
    st.stop()
common.run_header(bundle)
if not common.require_trades(bundle):
    st.stop()

trades = bundle["trades_enriched"]
tags = bundle["tags"]


def dimension_chart(frame, title: str, key: str = "key") -> None:
    if frame.is_empty():
        common.empty_state(f"No data for {title.lower()}.")
        return
    st.plotly_chart(
        charts.bar(
            frame,
            x=key,
            y="total_r",
            title=title,
            colors_by_sign=True,
            height=330,
        ),
        use_container_width=True,
        config={"displayModeBar": False},
    )


row1 = st.columns(3)
with row1[0]:
    dimension_chart(data.breakdown_by(trades, "direction"), "Net R by direction")
with row1[1]:
    dimension_chart(data.breakdown_by(trades, "symbol"), "Net R by symbol")
with row1[2]:
    dimension_chart(data.breakdown_by(trades, "regime"), "Net R by regime")

row2 = st.columns(3)
with row2[0]:
    hourly = data.breakdown_by(trades, "entry_hour")
    dimension_chart(hourly, "Net R by entry hour (UTC)")
with row2[1]:
    dimension_chart(data.breakdown_by(trades, "exit_reason"), "Net R by exit reason")
with row2[2]:
    dimension_chart(data.breakdown_by_tag(trades, tags), "Net R by error tag")

st.divider()
st.subheader("Net R distribution")
left, right = st.columns([1.0, 1.0])
with left:
    histogram = go.Figure(
        go.Histogram(
            x=trades["net_r"].to_list(),
            nbinsx=min(60, max(10, trades.height // 2)),
            marker_color=common.COLORS["accent"],
            opacity=0.85,
        )
    )
    histogram.add_vline(x=0, line_dash="dot", line_color=common.COLORS["muted"])
    histogram.add_vline(
        x=float(trades["net_r"].mean()),
        line_dash="dash",
        line_color=common.COLORS["amber"],
        annotation_text="mean",
        annotation_position="top",
    )
    histogram.update_layout(title="Net R histogram", xaxis_title="net R", yaxis_title="trades")
    st.plotly_chart(
        common.style_fig(histogram, height=380, showlegend=False),
        use_container_width=True,
        config={"displayModeBar": False},
    )
with right:
    box = go.Figure()
    for side, color in (("LONG", common.COLORS["long"]), ("SHORT", common.COLORS["short"])):
        subset = trades.filter(trades["side"] == side)
        if subset.is_empty():
            continue
        box.add_trace(
            go.Box(
                y=subset["net_r"].to_list(),
                name=side,
                marker_color=color,
                boxpoints="outliers",
            )
        )
    if box.data:
        box.add_hline(y=0, line_dash="dot", line_color=common.COLORS["muted"])
        box.update_layout(title="Net R by direction", yaxis_title="net R")
        st.plotly_chart(
            common.style_fig(box, height=380),
            use_container_width=True,
            config={"displayModeBar": False},
        )
    else:
        common.empty_state("No side labels on these trades.")

st.divider()
st.subheader("Detail tables")
with st.expander("Dimension tables (sortable)", expanded=False):
    for label, frame in [
        ("Direction", data.breakdown_by(trades, "direction")),
        ("Symbol", data.breakdown_by(trades, "symbol")),
        ("Regime", data.breakdown_by(trades, "regime")),
        ("Entry hour", data.breakdown_by(trades, "entry_hour")),
        ("Exit reason", data.breakdown_by(trades, "exit_reason")),
        ("Error tag", data.breakdown_by_tag(trades, tags)),
    ]:
        if frame.is_empty():
            continue
        st.markdown(f"**{label}**")
        st.dataframe(
            frame.drop("sort_key").to_pandas(),
            use_container_width=True,
            hide_index=True,
        )
