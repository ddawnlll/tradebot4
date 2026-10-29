"""Trade enrichment: post-exit drift and hypothetical outcomes of rejects.

Hypothetical results are never added to realized PnL. They exist to answer
"what did the filter miss, and what did it avoid", which is a learning signal,
not a performance claim.
"""

from __future__ import annotations

import json

import polars as pl

from bot.data.loader import load_bars_frame
from bot.data.storage import Paths

TIMEFRAME_MS = 15 * 60_000


def post_exit_drift(trades: pl.DataFrame, paths: Paths | None = None, horizons_h: tuple[int, ...] = (1, 4, 24)) -> pl.DataFrame:
    if trades.height == 0:
        return pl.DataFrame({"trade_id": [], **{f"drift_{h}h_r": [] for h in horizons_h}})
    paths = paths or Paths()
    rows = []
    for t in trades.iter_rows(named=True):
        risk = max(float(t["risk_usdt"]), 1e-9)
        sign = 1.0 if t["side"] == "LONG" else -1.0
        row = {"trade_id": t["trade_id"]}
        for h in horizons_h:
            target = int(t["exit_ts_ms"]) + h * 3_600_000
            df = load_bars_frame(paths, t["symbol"], int(t["exit_ts_ms"]), target)
            if df.height == 0:
                row[f"drift_{h}h_r"] = None
            else:
                px = float(df["close"][-1])
                row[f"drift_{h}h_r"] = (px - float(t["exit_price"])) * sign * float(t["quantity"]) / risk
        rows.append(row)
    return pl.DataFrame(rows)


def missed_opportunities(
    signals: pl.DataFrame,
    paths: Paths | None = None,
    *,
    max_signals: int = 400,
    horizon_ms: int = 8 * 3_600_000,
) -> pl.DataFrame:
    if signals.height == 0:
        return pl.DataFrame()
    paths = paths or Paths()
    cand = signals.filter(pl.col("action") == "REJECT").sort("score", descending=True, nulls_last=True).head(max_signals)
    rows = []
    for s in cand.iter_rows(named=True):
        support, resistance = s.get("support"), s.get("resistance")
        if support is None or resistance is None:
            continue
        feats = _parse(s.get("features"))
        atr = feats.get("atr")
        close = feats.get("close")
        if not atr or not close or atr <= 0 or resistance <= support:
            continue
        width = resistance - support
        side = None
        if close <= support + 0.25 * width:
            side = "LONG"
            stop = support - 0.35 * atr
            tp = (support + resistance) / 2
        elif close >= resistance - 0.25 * width:
            side = "SHORT"
            stop = resistance + 0.35 * atr
            tp = (support + resistance) / 2
        if side is None:
            continue
        risk_unit = abs(close - stop)
        if risk_unit <= 0:
            continue
        outcome = _scan(symbol=s["symbol"], start_ms=int(s["ts_ms"]), end_ms=int(s["ts_ms"]) + horizon_ms, paths=paths, side=side, entry=close, stop=stop, tp=tp)
        if outcome is None:
            continue
        r = outcome
        label = "MISSED_WIN" if r >= 1.0 else ("AVOIDED_LOSS" if r <= -1.0 else "NEUTRAL")
        rows.append(
            {
                "ts_ms": s["ts_ms"],
                "symbol": s["symbol"],
                "reject_reason": s.get("reject_reason"),
                "score": s.get("score"),
                "side": side,
                "hypothetical_r": r,
                "label": label,
            }
        )
    return pl.DataFrame(rows) if rows else pl.DataFrame()


def _scan(*, symbol: str, start_ms: int, end_ms: int, paths: Paths, side: str, entry: float, stop: float, tp: float) -> float | None:
    df = load_bars_frame(paths, symbol, start_ms, end_ms)
    if df.height == 0:
        return None
    sign = 1.0 if side == "LONG" else -1.0
    risk = abs(entry - stop)
    for row in df.iter_rows(named=True):
        high, low = float(row["high"]), float(row["low"])
        hit_stop = low <= stop if side == "LONG" else high >= stop
        hit_tp = high >= tp if side == "LONG" else low <= tp
        if hit_stop and hit_tp:
            return -1.0  # conservative STOP_FIRST
        if hit_stop:
            return (stop - entry) * sign / risk
        if hit_tp:
            return (tp - entry) * sign / risk
    px = float(df["close"][-1])
    return (px - entry) * sign / risk


def _parse(value) -> dict:
    if isinstance(value, dict):
        return value
    if not value:
        return {}
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return {}
