from __future__ import annotations

import plotly.graph_objects as go
import streamlit as st

from dashboard import charts, common, data

common.page_title(
    "Execution Quality",
    "Expected versus actual entry price, maker and taker mix, fees, slippage and order rejections.",
)
bundle = common.require_run()
if bundle is None:
    st.stop()
common.run_header(bundle)
if not common.require_trades(bundle):
    st.stop()

execution = bundle["execution"]
summary = data.execution_summary(execution)
fills = bundle["fills"]
metrics = bundle["metrics"]
orders = bundle["orders"]
metadata = bundle["metadata"]
values = {row["metric"]: row["value"] for row in summary.to_dicts()} if not summary.is_empty() else {}

common.metric_row(
    [
        ("Trades with fill", f"{int(values.get('trades_with_fill', 0)):,}", "matched ENTRY fills"),
        ("Mean slippage", f"{common.fmt_num(values.get('mean_slippage_bps'))} bps", "positive is adverse"),
        ("Median slippage", f"{common.fmt_num(values.get('median_slippage_bps'))} bps", "positive is adverse"),
        ("P95 |slippage|", f"{common.fmt_num(values.get('p95_abs_slippage_bps'))} bps", "tail"),
        ("Adverse fills", common.fmt_pct(values.get("pct_adverse_fills"), already_pct=True), "worse than expected"),
        ("Slippage cost", common.fmt_num(values.get("total_slippage_usdt"), 4), "USDT"),
        ("Entry fees", common.fmt_num(values.get("total_entry_fees"), 4) if not summary.is_empty() else common.fmt_num(metrics.get("fees"), 4), "USDT"),
        ("Total fees", common.fmt_num(metrics.get("fees"), 4), "whole run"),
    ]
)

if summary.is_empty():
    common.empty_state(
        "No ENTRY fills matched these trades. fills.parquet may be missing, or signal ids do not line up."
    )

st.divider()
left, right = st.columns([1.0, 1.0])
with left:
    filled = execution.filter(execution["has_fill"]) if not execution.is_empty() else execution
    if filled.is_empty():
        common.empty_state("No expected versus actual price pairs to plot.")
    else:
        scatter = go.Figure(
            go.Scatter(
                x=filled["entry_price"].to_list(),
                y=filled["actual_entry_price"].to_list(),
                mode="markers",
                marker={
                    "color": common.COLORS["accent"],
                    "size": 8,
                    "opacity": 0.8,
                },
                text=filled["trade_id"].to_list(),
                hovertemplate="%{text}<br>expected %{x}<br>actual %{y}<extra></extra>",
            )
        )
        low = min(float(filled["entry_price"].min()), float(filled["actual_entry_price"].min()))
        high = max(float(filled["entry_price"].max()), float(filled["actual_entry_price"].max()))
        scatter.add_trace(
            go.Scatter(
                x=[low, high],
                y=[low, high],
                mode="lines",
                name="perfect fill",
                line={"color": common.COLORS["muted"], "dash": "dot"},
            )
        )
        scatter.update_layout(
            title="Expected versus actual entry price",
            xaxis_title="expected (trades.entry_price)",
            yaxis_title="actual (first ENTRY fill)",
        )
        st.plotly_chart(
            common.style_fig(scatter, height=400),
            width="stretch",
            config={"displayModeBar": False},
        )

with right:
    if filled.is_empty():
        common.empty_state("No slippage observations.")
    else:
        histogram = go.Figure(
            go.Histogram(
                x=filled["slippage_bps"].to_list(),
                nbinsx=min(50, max(10, filled.height)),
                marker_color=common.COLORS["amber"],
                opacity=0.85,
            )
        )
        histogram.add_vline(x=0, line_dash="dot", line_color=common.COLORS["muted"])
        histogram.update_layout(
            title="Entry slippage distribution (bps, positive is adverse)",
            xaxis_title="bps",
            yaxis_title="trades",
        )
        st.plotly_chart(
            common.style_fig(histogram, height=400, showlegend=False),
            width="stretch",
            config={"displayModeBar": False},
        )

st.divider()
left, right = st.columns([1.0, 1.0])
with left:
    by_symbol = data.execution_by_symbol(execution)
    if by_symbol.is_empty():
        common.empty_state("No slippage by symbol.")
    else:
        st.plotly_chart(
            charts.bar(
                by_symbol,
                x="symbol",
                y="mean_slippage_bps",
                title="Mean entry slippage by symbol (bps)",
                colors_by_sign=True,
                height=350,
            ),
            width="stretch",
            config={"displayModeBar": False},
        )
with right:
    mix = data.liquidity_mix(fills)
    if mix.is_empty():
        common.empty_state("No fills to classify as maker or taker.")
    else:
        donut = go.Figure(
            go.Pie(
                labels=mix["liquidity"].to_list(),
                values=mix["fills"].to_list(),
                hole=0.58,
                marker_colors=[common.COLORS["green"], common.COLORS["amber"], common.COLORS["muted"]],
            )
        )
        donut.update_layout(title="Maker versus taker fills")
        st.plotly_chart(
            common.style_fig(donut, height=350),
            width="stretch",
            config={"displayModeBar": False},
        )
        st.dataframe(mix.to_pandas(), width="stretch", hide_index=True)

st.divider()
st.subheader("Fees")
fees = data.fee_breakdown(fills)
if fees.is_empty():
    common.empty_state("No fills with fee data.")
else:
    st.dataframe(
        fees.to_pandas(),
        width="stretch",
        hide_index=True,
        column_config={
            "fee": st.column_config.NumberColumn("Fee", format="%.6f"),
            "fee_bps": st.column_config.NumberColumn("Fee bps", format="%.2f"),
            "notional": st.column_config.NumberColumn("Notional", format="%.2f"),
        },
    )

st.divider()
st.subheader("Orders and rejections")
left, right = st.columns([1.0, 1.0])
with left:
    health = data.order_health(metadata, orders, fills, bundle["signals"])
    st.dataframe(health.to_pandas(), width="stretch", hide_index=True)
    latency = health.filter(health["metric"] == "latency_ms")
    denied = health.filter(health["metric"] == "denied_orders")
    if not latency.is_empty() and latency["value"][0] is not None:
        if float(latency["value"][0]) == 0:
            st.caption("Latency: configured as 0 ms, so fills are a zero-latency simulation. No live latency data.")
        else:
            st.caption(f"Latency: {latency['value'][0]:.0f} ms from run config.")
    if not denied.is_empty() and denied["value"][0] is None:
        st.caption("Denied order count is not exported by this backtest adapter.")
with right:
    rejections = data.reject_counts_table(metadata)
    if rejections.is_empty():
        common.empty_state("No metadata reject_counts in this run.")
    else:
        st.plotly_chart(
            charts.bar(
                rejections.head(10),
                x="reason",
                y="count",
                title="Entry rejections by reason",
                color=common.COLORS["red"],
                height=360,
            ),
            width="stretch",
            config={"displayModeBar": False},
        )

st.divider()
st.subheader("Per-trade execution")
if execution.is_empty():
    common.empty_state("No execution table for this run.")
else:
    st.dataframe(
        execution.select(
            "trade_id",
            "symbol",
            "side",
            "entry_price",
            "actual_entry_price",
            "slippage_bps",
            "slippage_usdt",
            "entry_fee",
            "entry_liquidity",
            "exit_reason",
            "net_r",
        ).to_pandas(),
        width="stretch",
        hide_index=True,
    )
