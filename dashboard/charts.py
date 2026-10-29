"""Plotly chart builders shared by pages."""

from __future__ import annotations

from typing import Any

import plotly.graph_objects as go
import polars as pl
from dashboard.common import COLORS, style_fig
from plotly.subplots import make_subplots


def candlestick(
    bars: pl.DataFrame,
    *,
    title: str,
    levels: list[dict[str, Any]] | None = None,
    markers: list[dict[str, Any]] | None = None,
    height: int = 520,
    time_col: str = "dt",
) -> go.Figure:
    """Candles with horizontal levels and event markers.

    levels: dicts with price, label, color, dash.
    markers: dicts with ts, price, label, color, symbol.
    """
    figure = go.Figure(
        go.Candlestick(
            x=bars[time_col].to_list(),
            open=bars["open"].to_list(),
            high=bars["high"].to_list(),
            low=bars["low"].to_list(),
            close=bars["close"].to_list(),
            increasing_line_color=COLORS["green"],
            increasing_fillcolor="rgba(63,185,80,0.35)",
            decreasing_line_color=COLORS["red"],
            decreasing_fillcolor="rgba(248,81,73,0.35)",
            name="price",
            showlegend=False,
        )
    )
    for level in levels or []:
        price = level.get("price")
        if price is None:
            continue
        color = level.get("color", COLORS["muted"])
        start = level.get("start")
        if start is not None:
            end = level.get("end") or bars[time_col][-1]
            figure.add_shape(
                type="line",
                x0=start,
                x1=end,
                y0=float(price),
                y1=float(price),
                line={"color": color, "dash": level.get("dash", "dot"), "width": 1.4},
            )
            figure.add_annotation(
                x=start,
                y=float(price),
                text=level.get("label", ""),
                showarrow=False,
                xanchor="left",
                yanchor="bottom",
                font={"color": color, "size": 10},
                bgcolor="rgba(11,15,20,0.65)",
            )
        else:
            figure.add_hline(
                y=float(price),
                line_color=color,
                line_dash=level.get("dash", "dot"),
                line_width=1.4,
                annotation_text=level.get("label", ""),
                annotation_position=level.get("position", "right"),
                annotation_font_color=color,
            )
    for marker in markers or []:
        figure.add_trace(
            go.Scatter(
                x=[marker["ts"]],
                y=[float(marker["price"])],
                mode="markers+text",
                marker={
                    "size": marker.get("size", 11),
                    "color": marker.get("color", COLORS["accent"]),
                    "symbol": marker.get("symbol", "circle"),
                    "line": {"width": 1, "color": "#0b0f14"},
                },
                text=[marker.get("label", "")],
                textposition=marker.get("textposition", "top center"),
                textfont={"size": 10, "color": marker.get("color", COLORS["text"])},
                name=marker.get("label", "event"),
                showlegend=marker.get("showlegend", True),
                hovertemplate="%{text}<br>%{y}<extra></extra>",
            )
        )
    figure.update_layout(title=title, xaxis_rangeslider_visible=False, hovermode="x unified")
    style_fig(figure, height=height)
    figure.update_yaxes(title_text="price")
    return figure


def equity_drawdown_chart(equity: pl.DataFrame, starting_balance: float | None) -> go.Figure:
    figure = make_subplots(
        rows=2,
        cols=1,
        shared_xaxes=True,
        row_heights=[0.68, 0.32],
        vertical_spacing=0.06,
        subplot_titles=("Equity", "Drawdown"),
    )
    figure.add_trace(
        go.Scatter(
            x=equity["dt"].to_list(),
            y=equity["equity"].to_list(),
            mode="lines",
            name="equity",
            line={"color": COLORS["accent"], "width": 2},
            fill="tozeroy",
            fillcolor="rgba(88,166,255,0.08)",
        ),
        row=1,
        col=1,
    )
    if starting_balance is not None:
        figure.add_hline(
            y=float(starting_balance),
            line_dash="dot",
            line_color=COLORS["muted"],
            annotation_text="starting balance",
            annotation_position="right",
            row=1,
            col=1,
        )
    figure.add_trace(
        go.Scatter(
            x=equity["dt"].to_list(),
            y=(equity["drawdown_pct"] * 100.0).to_list(),
            mode="lines",
            name="drawdown %",
            line={"color": COLORS["red"], "width": 1.4},
            fill="tozeroy",
            fillcolor="rgba(248,81,73,0.16)",
        ),
        row=2,
        col=1,
    )
    figure.update_yaxes(title_text="USDT", row=1, col=1)
    figure.update_yaxes(title_text="%", row=2, col=1)
    return style_fig(figure, height=520, showlegend=False)


def bar(
    df: pl.DataFrame,
    *,
    x: str,
    y: str,
    title: str,
    color: str | None = None,
    colors_by_sign: bool = False,
    height: int = 360,
    text: str | None = None,
) -> go.Figure:
    if colors_by_sign:
        colors = [
            COLORS["green"] if (value or 0) >= 0 else COLORS["red"]
            for value in df[y].to_list()
        ]
    else:
        colors = color or COLORS["accent"]
    figure = go.Figure(
        go.Bar(
            x=df[x].to_list(),
            y=df[y].to_list(),
            marker_color=colors,
            text=df[text].to_list() if text else None,
            textposition="outside",
        )
    )
    figure.update_layout(title=title)
    return style_fig(figure, height=height, showlegend=False)
