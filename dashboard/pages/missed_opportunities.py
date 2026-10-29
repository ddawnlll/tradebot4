from __future__ import annotations

from datetime import UTC, datetime

import plotly.graph_objects as go
import streamlit as st

from dashboard import cache, charts, common, data

common.page_title(
    "Missed Opportunities",
    "Rejected signals grouped by reason, with clearly labelled hypothetical outcomes. Nothing here is an executed trade.",
)
bundle = common.require_run()
if bundle is None:
    st.stop()
common.run_header(bundle)

signals = bundle["signals"]
if signals.is_empty():
    common.empty_state("This run has no signals.parquet, so rejected signals cannot be analysed.")
    st.stop()

rejected_all = signals.filter(signals["action"] == "REJECT")
if rejected_all.is_empty():
    common.empty_state("No rejected signals in this run.")
    st.stop()

summary = data.rejected_summary(signals)
common.metric_row(
    [
        ("Rejected signals", f"{rejected_all.height:,}", "action == REJECT"),
        ("Reject reasons", str(summary.height), "distinct tags"),
        ("Top reason", summary["reject_reason"][0], f"{summary['share_pct'][0]:.1f}% of rejects"),
        ("Median score", common.fmt_num(rejected_all["score"].median(), 3), "recorded score"),
    ]
)
st.warning(
    "Hypothetical outcomes below are counterfactuals, not trades. They infer a side from range "
    "location, ignore fees, funding, slippage and position limits, and use a fixed +2R / -1R path.",
    icon=":material/science:",
)

st.divider()
left, right = st.columns([1.0, 1.0])
with left:
    figure = go.Figure(
        go.Bar(
            x=summary["count"].to_list(),
            y=summary["reject_reason"].to_list(),
            orientation="h",
            marker_color=common.COLORS["red"],
            text=[
                f"{count:,} ({share:.1f}%)"
                for count, share in zip(summary["count"].to_list(), summary["share_pct"].to_list(), strict=False)
            ],
            textposition="outside",
        )
    )
    figure.update_layout(
        title="Rejected signals by reason",
        xaxis_title="signals",
        yaxis={"autorange": "reversed"},
    )
    st.plotly_chart(
        common.style_fig(figure, height=400, showlegend=False),
        width="stretch",
        config={"displayModeBar": False},
    )
with right:
    st.dataframe(
        summary.to_pandas(),
        width="stretch",
        hide_index=True,
        column_config={
            "reject_reason": "Reason",
            "count": st.column_config.NumberColumn("Count", format="%d"),
            "share_pct": st.column_config.NumberColumn("Share", format="%.2f%%"),
            "avg_score": st.column_config.NumberColumn("Avg score", format="%.3f"),
            "p90_score": st.column_config.NumberColumn("P90 score", format="%.3f"),
            "example_reason": "Example",
            "symbols": st.column_config.NumberColumn("Symbols", format="%d"),
        },
    )

st.divider()
st.subheader("Hypothetical filter")
control_cols = st.columns([2.0, 1.0, 1.0])
with control_cols[0]:
    reasons = st.multiselect(
        "Reject reasons",
        summary["reject_reason"].to_list(),
        default=summary["reject_reason"].to_list(),
    )
with control_cols[1]:
    min_score = st.slider("Minimum score", 0.0, 1.0, 0.55, 0.01)
with control_cols[2]:
    max_signals = st.slider("Signals to simulate", 5, 40, 20, 5)

filtered = rejected_all
if reasons:
    filtered = filtered.filter(filtered["reject_reason"].is_in(reasons))
if "score" in filtered.columns:
    filtered = filtered.filter(filtered["score"].is_null() | (filtered["score"] >= min_score))
if filtered.is_empty():
    common.empty_state("No rejected signals match these filters.")
    st.stop()

candidates = data.hypothetical_candidates(filtered, max_signals=max_signals)
home = str(data.tradebot_home())
horizon_ms = 240 * 60_000
bars_by_ts = {}
reason_by_key = {}
for row in candidates.iter_rows(named=True):
    ts = int(row["ts_ms"])
    key = (ts, row["symbol"])
    bars_by_ts[key] = cache.load_bars(row["symbol"], ts, ts + horizon_ms, home)
    reason_by_key[key] = row.get("reject_reason")

results = data.hypothetical_outcomes(
    candidates,
    {},
    bars_by_ts=bars_by_ts,
    max_signals=max_signals,
    horizon_bars=240,
)
result_summary = data.hypothetical_summary(results)
valid = results.filter(results["valid"]) if not results.is_empty() else results
tradable = valid.filter(valid["outcome"].is_in(["TARGET", "STOP", "OPEN"])) if not valid.is_empty() else valid

common.metric_row(
    [
        ("Simulated", f"{results.height:,}", "sampled rejected signals"),
        ("With market data", f"{valid.height:,}", "bars found for the window"),
        ("Target hit", f"{int((tradable['outcome'] == 'TARGET').sum()) if not tradable.is_empty() else 0:,}", "would reach +2R first"),
        ("Stopped", f"{int((tradable['outcome'] == 'STOP').sum()) if not tradable.is_empty() else 0:,}", "would reach -1R first"),
        (
            "Mean hypothetical R",
            f"{common.fmt_num(tradable['r_multiple'].mean())} R" if not tradable.is_empty() else "n/a",
            "target or stop or open mark",
        ),
        (
            "Total hypothetical R",
            f"{common.fmt_num(tradable['r_multiple'].sum())} R" if not tradable.is_empty() else "n/a",
            "not additive to strategy PnL",
        ),
    ]
)
if not result_summary.is_empty():
    st.caption("Outcome mix: " + " · ".join(
        f"{row['outcome']} {row['count']}" for row in result_summary.iter_rows(named=True)
    ))

if valid.is_empty():
    common.empty_state(
        "No market bars were found for the sampled windows. The canonical 1m store is required "
        "for hypothetical price paths."
    )

st.divider()
st.subheader("Hypothetical results")
table = data.hypothetical_with_context(results, candidates)
if table.is_empty():
    common.empty_state("No hypothetical rows.")
else:
    st.dataframe(
        table.to_pandas(),
        width="stretch",
        hide_index=True,
        column_config={
            "signal_ts_ms": st.column_config.DatetimeColumn("Signal time", format="YYYY-MM-DD HH:mm"),
            "symbol": "Symbol",
            "reject_reason": "Reject reason",
            "score": st.column_config.NumberColumn("Score", format="%.3f"),
            "side": "Inferred side",
            "outcome": "Outcome",
            "r_multiple": st.column_config.NumberColumn("Hypo R", format="%+.2f"),
            "bars_held": st.column_config.NumberColumn("Bars", format="%d"),
            "entry_price": st.column_config.NumberColumn("Entry", format="%.6f"),
            "stop_price": st.column_config.NumberColumn("Stop", format="%.6f"),
            "target_price": st.column_config.NumberColumn("Target", format="%.6f"),
            "note": "Note",
        },
    )

st.divider()
st.subheader("Post-signal price path")
if valid.is_empty():
    common.empty_state("No bars available to draw a price path.")
else:
    labels = {
        (int(row["signal_ts_ms"]), row["symbol"]): (
            f"{datetime.fromtimestamp(int(row['signal_ts_ms']) / 1000, tz=UTC):%Y-%m-%d %H:%M} "
            f"{row['symbol']} · {reason_by_key.get((int(row['signal_ts_ms']), row['symbol']), 'n/a')} "
            f"· {row['side']} · {row['outcome']}"
        )
        for row in valid.iter_rows(named=True)
    }
    selected_key = st.selectbox(
        "Rejected signal",
        list(labels),
        format_func=lambda value: labels.get(value, str(value)),
    )
    selected_ts, selected_symbol = selected_key
    bars = bars_by_ts.get(selected_key)
    row = results.filter(
        (results["signal_ts_ms"] == selected_ts) & (results["symbol"] == selected_symbol)
    ).row(0, named=True)
    if bars is None or bars.is_empty():
        common.empty_state("No bars for this window.")
    else:
        marker_ts = datetime.fromtimestamp(int(selected_ts) / 1000, tz=UTC)
        levels = []
        for price, label, color in [
            (row.get("entry_price"), "hypothetical entry", common.COLORS["accent"]),
            (row.get("stop_price"), "-1R stop", common.COLORS["red"]),
            (row.get("target_price"), "+2R target", common.COLORS["green"]),
        ]:
            if price is not None:
                levels.append(
                    {
                        "price": price,
                        "label": label,
                        "color": color,
                        "dash": "dash",
                        "start": marker_ts,
                    }
                )
        markers = []
        if row.get("entry_price") is not None:
            markers.append(
                {
                    "ts": marker_ts,
                    "price": row["entry_price"],
                    "label": "hypothetical entry",
                    "color": common.COLORS["accent"],
                    "symbol": "triangle-up" if row["side"] == "LONG" else "triangle-down",
                }
            )
        figure = charts.candlestick(
            bars,
            title=f"{row['symbol']} · rejected signal price path",
            levels=levels,
            markers=markers,
        )
        st.plotly_chart(figure, width="stretch", config={"displayModeBar": False})
        st.caption(
            f"Outcome `{row['outcome']}` after {row['bars_held']} bars, hypothetical R "
            f"{common.fmt_num(row['r_multiple'])}. {row['note']}."
        )
