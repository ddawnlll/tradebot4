from __future__ import annotations

import plotly.graph_objects as go
import streamlit as st

from dashboard import charts, common, data

common.page_title(
    "Overview",
    "Equity, drawdown, return distribution and long versus short performance for the selected run.",
)
bundle = common.require_run()
if bundle is None:
    st.stop()

common.run_header(bundle)
if not common.require_trades(bundle):
    st.stop()

trades = bundle["trades"]
equity = bundle["equity"]
metrics = bundle["metrics"]
starting_balance = metrics.get("starting_balance")
if starting_balance is None and not equity.is_empty():
    starting_balance = float(equity["equity"][0])

net_pnl = metrics.get("net_pnl", float(trades["net_pnl"].sum()))
return_pct = metrics.get("return_pct")
if return_pct is None and starting_balance:
    return_pct = (float(trades["net_pnl"].sum()) / float(starting_balance)) * 100.0
win_rate = metrics.get("win_rate", float(trades["win"].mean()))
expectancy = metrics.get("expectancy_r", float(trades["net_r"].mean()))
profit_factor = metrics.get("profit_factor")
if profit_factor is None:
    gains = float(trades.filter(trades["net_r"] > 0)["net_r"].sum())
    losses = abs(float(trades.filter(trades["net_r"] < 0)["net_r"].sum()))
    profit_factor = gains / losses if losses else None
max_dd = metrics.get("max_drawdown_pct")
if max_dd is None and not equity.is_empty():
    max_dd = abs(float(equity["drawdown_pct"].min())) * 100.0

common.metric_row(
    [
        ("Trades", f"{trades.height:,}", None),
        ("Net PnL", common.fmt_num(net_pnl), "USDT"),
        ("Return", common.fmt_pct(return_pct, already_pct=True), "of starting balance"),
        ("Win rate", common.fmt_pct(win_rate), "net PnL > 0"),
        ("Expectancy", f"{common.fmt_num(expectancy)} R", "mean net R per trade"),
        ("Profit factor", common.fmt_num(profit_factor), "gross wins / gross losses"),
        ("Max drawdown", common.fmt_pct(max_dd, already_pct=True), "peak to trough"),
        ("Sharpe", common.fmt_num(metrics.get("sharpe")), "per-run metric"),
    ]
)

st.write("")
left, right = st.columns([1.65, 1.0])

with left:
    if equity.is_empty():
        common.empty_state("This run has no equity.parquet, so the equity curve cannot be drawn.")
    else:
        st.plotly_chart(
            charts.equity_drawdown_chart(equity, starting_balance),
            width="stretch",
            config={"displayModeBar": False},
        )

with right:
    returns = data.trade_returns(trades)
    figure = go.Figure()
    figure.add_trace(
        go.Scatter(
            x=returns["entry_dt"].to_list(),
            y=returns["cumulative_r"].to_list(),
            mode="lines",
            name="cumulative R",
            line={"color": common.COLORS["purple"], "width": 2},
            fill="tozeroy",
            fillcolor="rgba(188,140,255,0.10)",
        )
    )
    figure.add_hline(y=0, line_dash="dot", line_color=common.COLORS["muted"])
    figure.update_layout(title="Cumulative net R by entry time")
    st.plotly_chart(
        common.style_fig(figure, height=520, showlegend=False),
        width="stretch",
        config={"displayModeBar": False},
    )

st.divider()
st.subheader("Return distribution")
left, right = st.columns([1.0, 1.0])
with left:
    histogram = go.Figure(
        go.Histogram(
            x=trades["net_r"].to_list(),
            nbinsx=min(50, max(10, trades.height // 2)),
            marker_color=common.COLORS["purple"],
            opacity=0.85,
        )
    )
    histogram.add_vline(x=0, line_dash="dot", line_color=common.COLORS["muted"])
    histogram.update_layout(title="Net R per trade", xaxis_title="net R", yaxis_title="trades")
    st.plotly_chart(
        common.style_fig(histogram, height=320, showlegend=False),
        width="stretch",
        config={"displayModeBar": False},
    )
with right:
    pnl_figure = go.Figure(
        go.Bar(
            x=list(range(1, trades.height + 1)),
            y=returns["net_pnl"].to_list(),
            marker_color=[
                common.COLORS["green"] if value >= 0 else common.COLORS["red"]
                for value in returns["net_pnl"].to_list()
            ],
        )
    )
    pnl_figure.add_hline(y=0, line_dash="dot", line_color=common.COLORS["muted"])
    pnl_figure.update_layout(
        title="Net PnL per trade, chronological",
        xaxis_title="trade number",
        yaxis_title="USDT",
    )
    st.plotly_chart(
        common.style_fig(pnl_figure, height=320, showlegend=False),
        width="stretch",
        config={"displayModeBar": False},
    )

st.divider()
st.subheader("Long versus short")

summary = data.long_short_summary(trades)
if summary.is_empty():
    common.empty_state("No directional trade data in this run.")
else:
    st.dataframe(
        summary.to_pandas(),
        width="stretch",
        hide_index=True,
        column_config={
            "side": "Side",
            "trades": st.column_config.NumberColumn("Trades", format="%d"),
            "net_pnl": st.column_config.NumberColumn("Net PnL", format="%.2f"),
            "total_r": st.column_config.NumberColumn("Total R", format="%.2f"),
            "avg_r": st.column_config.NumberColumn("Avg R", format="%.3f"),
            "win_rate": st.column_config.ProgressColumn(
                "Win rate", min_value=0.0, max_value=1.0, format="%.1f%%"
            ),
            "profit_factor": st.column_config.NumberColumn("Profit factor", format="%.2f"),
            "tp1_rate": st.column_config.NumberColumn("TP1 rate", format="%.1f%%"),
            "stop_rate": st.column_config.NumberColumn("Stop rate", format="%.1f%%"),
            "funding": st.column_config.NumberColumn("Funding", format="%.4f"),
        },
    )
    pnl_figure = charts.bar(
        summary,
        x="side",
        y="net_pnl",
        title="Net PnL by direction",
        colors_by_sign=True,
        height=300,
    )
    st.plotly_chart(pnl_figure, width="stretch", config={"displayModeBar": False})

st.divider()
st.subheader("Run composition")
meta = bundle["metadata"]
config = meta.get("config") if isinstance(meta.get("config"), dict) else {}
symbols = config.get("symbols") or sorted(bundle["trades"]["symbol"].unique().to_list())
start_ms = config.get("start_ms")
end_ms = config.get("end_ms")
columns = st.columns(4)
columns[0].metric("Symbols", len(symbols))
columns[1].metric("Fees", common.fmt_num(metrics.get("fees"), 4))
columns[2].metric("Funding", common.fmt_num(metrics.get("funding"), 4))
columns[3].metric("Signals", f"{bundle['signals'].height:,}" if not bundle["signals"].is_empty() else "n/a")
if start_ms and end_ms:
    st.caption(f"Window `{start_ms}` to `{end_ms}` (epoch ms). Symbol set: {', '.join(symbols)}")
