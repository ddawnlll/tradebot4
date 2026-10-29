from __future__ import annotations

import json
from datetime import UTC, datetime

import polars as pl
import streamlit as st

from dashboard import cache, charts, common, data

common.page_title(
    "Trade Replay",
    "One trade on 1m and 15m candles. Support and resistance come only from the setup recorded at or before entry.",
)
bundle = common.require_run()
if bundle is None:
    st.stop()
common.run_header(bundle)
if not common.require_trades(bundle):
    st.stop()

trades = bundle["trades"].sort("entry_ts_ms", descending=True)
labels = {
    row["trade_id"]: (
        f"{row['entry_dt']:%Y-%m-%d %H:%M} UTC · {row['symbol']} · {row['side']} · "
        f"{row['net_r']:+.2f}R · {row['exit_reason']}"
    )
    for row in trades.iter_rows(named=True)
}
trade_id = st.selectbox(
    "Trade",
    trades["trade_id"].to_list(),
    format_func=lambda value: labels.get(value, value),
)
trade = trades.filter(pl.col("trade_id") == trade_id).row(0, named=True)

setup = data.setup_for_trade(
    bundle["signals"],
    signal_id=trade.get("signal_id"),
    symbol=trade.get("symbol"),
    entry_ts_ms=trade.get("entry_ts_ms"),
)
levels = data.trade_levels(
    bundle["orders"],
    signal_id=trade.get("signal_id"),
    symbol=trade.get("symbol"),
    entry_ts_ms=trade.get("entry_ts_ms"),
)
trade_fills = data.fills_for_trade(
    bundle["fills"],
    signal_id=trade.get("signal_id"),
    symbol=trade.get("symbol"),
    entry_ts_ms=trade.get("entry_ts_ms"),
    exit_ts_ms=trade.get("exit_ts_ms"),
)
trade_tags = (
    bundle["tags"].filter(pl.col("trade_id") == trade_id)
    if not bundle["tags"].is_empty()
    else bundle["tags"]
)

common.metric_row(
    [
        ("Net R", f"{common.fmt_num(trade['net_r'])} R", "realized"),
        ("Net PnL", common.fmt_num(trade["net_pnl"]), "USDT"),
        ("Setup score", common.fmt_num(trade["setup_score"], 3), "recorded at entry"),
        ("MAE", f"{common.fmt_num(trade['mae_r'])} R", "worst excursion"),
        ("MFE", f"{common.fmt_num(trade['mfe_r'])} R", "best excursion"),
        ("Bars held", f"{int(trade['bars_held'] or 0):,}", "1m bars"),
        ("Fees", common.fmt_num(trade["fees"], 4), "USDT"),
        ("Funding", common.fmt_num(trade["funding"], 4), "USDT"),
    ]
)

context_cols = st.columns([1.0, 1.0, 1.0])
context_cols[0].metric("Entry", common.fmt_num(trade["entry_price"], 6))
context_cols[1].metric("Exit", common.fmt_num(trade["exit_price"], 6))
context_cols[2].metric("Risk", common.fmt_num(trade["risk_usdt"], 4) + " USDT")

if not setup.is_empty():
    st.caption(
        f"Levels from `{setup['level_source'][0]}` signal at "
        f"{datetime.fromtimestamp(int(setup['ts_ms'][0]) / 1000, tz=UTC):%Y-%m-%d %H:%M} UTC "
        f"({int(setup['age_ms'][0]) / 60_000:.1f} min before entry). No later signal is used."
    )
else:
    st.caption("No recorded ENTER setup at or before this entry. Support and resistance are not drawn.")

if not trade_tags.is_empty():
    pills = "".join(
        f"<span class='tag-pill'>{row['tag']}</span>" for row in trade_tags.iter_rows(named=True)
    )
    st.markdown(pills, unsafe_allow_html=True)
    notes = [row["notes"] for row in trade_tags.iter_rows(named=True) if row.get("notes")]
    if notes:
        st.caption("Notes: " + " | ".join(notes))
    if trade_tags["source"].unique().to_list() == ["derived"]:
        st.caption("Tags are derived from exit reason, MAE/MFE, funding and fees; no tags table exists in this run.")

entry_ms = int(trade["entry_ts_ms"])
exit_ms = int(trade["exit_ts_ms"])
start_ms = entry_ms - 4 * 60 * 60 * 1000
end_ms = max(exit_ms, entry_ms + 60 * 60 * 1000) + 60 * 60 * 1000
bars = cache.load_bars(trade["symbol"], start_ms, end_ms, str(data.tradebot_home()))

if bars.is_empty():
    common.empty_state(
        "No canonical 1m bars found for this symbol and window. "
        "Check `TRADEBOT_HOME/data/market/bars_1m`."
    )
    st.stop()

price_levels: list[dict] = []
if not setup.is_empty():
    setup_row = setup.row(0, named=True)
    if setup_row.get("support") is not None:
        price_levels.append(
            {
                "price": setup_row["support"],
                "label": "recorded support",
                "color": common.COLORS["green"],
                "dash": "dot",
                "start": trade["entry_dt"],
            }
        )
    if setup_row.get("resistance") is not None:
        price_levels.append(
            {
                "price": setup_row["resistance"],
                "label": "recorded resistance",
                "color": common.COLORS["red"],
                "dash": "dot",
                "start": trade["entry_dt"],
            }
        )

level_colors = {
    "STOP": common.COLORS["red"],
    "TP1": common.COLORS["green"],
    "TP2": common.COLORS["green"],
    "ENTRY": common.COLORS["accent"],
}
for row in levels.iter_rows(named=True):
    price_levels.append(
        {
            "price": row["price"],
            "label": f"{row['kind']} order",
            "color": level_colors.get(row["kind"], common.COLORS["muted"]),
            "dash": "dash",
            "start": trade["entry_dt"],
        }
    )

markers: list[dict] = [
    {
        "ts": trade["entry_dt"],
        "price": trade["entry_price"],
        "label": "entry",
        "color": common.COLORS["accent"],
        "symbol": "triangle-up" if trade["side"] == "LONG" else "triangle-down",
    },
    {
        "ts": trade["exit_dt"],
        "price": trade["exit_price"],
        "label": f"exit ({trade['exit_reason']})",
        "color": common.COLORS["amber"],
        "symbol": "x",
    },
]
for fill in trade_fills.iter_rows(named=True):
    color = level_colors.get(fill["kind"], common.COLORS["purple"])
    markers.append(
        {
            "ts": datetime.fromtimestamp(int(fill["ts_ms"]) / 1000, tz=UTC),
            "price": fill["price"],
            "label": f"{fill['kind']} {fill['liquidity'] or ''}".strip(),
            "color": color,
            "symbol": "circle",
            "size": 9,
            "showlegend": False,
        }
    )

one_min, fifteen_min = st.tabs(["1 minute", "15 minutes"])
with one_min:
    figure = charts.candlestick(
        bars,
        title=f"{trade['symbol']} · 1m",
        levels=price_levels,
        markers=markers,
    )
    figure.add_annotation(
        text=f"MAE {trade['mae_r']:+.2f}R &nbsp; MFE {trade['mfe_r']:+.2f}R",
        xref="paper",
        yref="paper",
        x=0.01,
        y=0.99,
        showarrow=False,
        align="left",
        bgcolor="rgba(11,15,20,0.75)",
        bordercolor=common.COLORS["grid"],
        font={"color": common.COLORS["text"], "size": 12},
    )
    st.plotly_chart(figure, use_container_width=True, config={"displayModeBar": False})

with fifteen_min:
    bars_15 = cache.resample_bars(bars, "15m")
    figure_15 = charts.candlestick(
        bars_15,
        title=f"{trade['symbol']} · 15m",
        levels=price_levels,
        markers=[
            marker
            for marker in markers
            if marker["ts"] >= bars_15["dt"][0] and marker["ts"] <= bars_15["dt"][-1]
        ],
    )
    st.plotly_chart(figure_15, use_container_width=True, config={"displayModeBar": False})

st.divider()
left, right = st.columns([1.4, 1.0])
with left:
    st.subheader("Fills")
    if trade_fills.is_empty():
        common.empty_state("No fills recorded for this trade (fills.parquet missing or unmatched).")
    else:
        st.dataframe(
            trade_fills.select(
                "ts_ms", "kind", "side", "price", "qty", "fee", "liquidity", "order_type"
            ).to_pandas(),
            use_container_width=True,
            hide_index=True,
        )
with right:
    st.subheader("Order levels")
    if levels.is_empty():
        common.empty_state("No STOP/TP legs found in orders.parquet for this entry.")
    else:
        st.dataframe(levels.to_pandas(), use_container_width=True, hide_index=True)
    if not setup.is_empty():
        st.markdown("**Recorded setup**")
        setup_row = setup.row(0, named=True)
        st.write(
            {
                "signal_id": setup_row.get("signal_id"),
                "regime": setup_row.get("regime"),
                "regime_confidence": setup_row.get("regime_confidence"),
                "score": setup_row.get("score"),
                "support": setup_row.get("support"),
                "resistance": setup_row.get("resistance"),
            }
        )
        if setup_row.get("features"):
            with st.expander("Raw features JSON", expanded=False):
                try:
                    st.json(json.loads(setup_row["features"]))
                except (json.JSONDecodeError, TypeError):
                    st.code(setup_row["features"])
