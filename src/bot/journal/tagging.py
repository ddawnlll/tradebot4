"""Rule-based error tagging.

Tags are hypotheses about *why* a trade lost, derived only from the recorded
trade and its execution facts. They are versioned: changing a rule changes
``RULE_VERSION`` and adds new tag rows; history is never rewritten.
"""

from __future__ import annotations

import polars as pl

RULE_VERSION = "1.0.0"

TAGS = (
    "SWEEP_STOP",
    "BREAKOUT_AGAINST",
    "FALSE_RANGE",
    "EARLY_EXIT",
    "TIME_STOP_FLAT",
    "HIGH_SLIPPAGE",
    "SQUEEZE",
    "MISSED_WIN",
    "AVOIDED_LOSS",
)


def tag_trades(trades: pl.DataFrame) -> pl.DataFrame:
    if trades.height == 0:
        return pl.DataFrame({"trade_id": [], "tag": [], "notes": [], "rule_version": []})
    rows: list[dict] = []

    def add(trade_id: str, tag: str, note: str) -> None:
        rows.append({"trade_id": trade_id, "tag": tag, "notes": note, "rule_version": RULE_VERSION})

    for t in trades.iter_rows(named=True):
        tid = t["trade_id"]
        reason = t["exit_reason"]
        net_r = float(t["net_r"])
        mae = float(t["mae_r"])
        mfe = float(t["mfe_r"])
        bars = int(t.get("bars_held") or 0)
        notional = abs(float(t["entry_price"]) * float(t["quantity"]))
        slip_bps = (float(t["slippage"]) / notional * 10_000.0) if notional > 0 else 0.0

        if reason in ("STOP", "STOP_BREAKEVEN") and mae < -1.05:
            add(tid, "SWEEP_STOP", f"MAE {mae:.2f}R beyond stop")
        if reason == "BREAKOUT":
            add(tid, "BREAKOUT_AGAINST", f"zone break exit after {bars} bars")
            if net_r < -0.5:
                add(tid, "FALSE_RANGE", f"range failed, {net_r:.2f}R")
            if mfe < 0.2:
                add(tid, "SQUEEZE", f"no favourable excursion before break (MFE {mfe:.2f}R)")
        if reason in ("TP1", "STOP_BREAKEVEN", "TIME_STOP") and mfe > max(net_r, 0) + 1.0:
            add(tid, "EARLY_EXIT", f"MFE {mfe:.2f}R vs realized {net_r:.2f}R")
        if reason == "TIME_STOP" and abs(net_r) < 0.25:
            add(tid, "TIME_STOP_FLAT", f"net {net_r:.2f}R")
        if slip_bps > 5.0:
            add(tid, "HIGH_SLIPPAGE", f"{slip_bps:.1f} bps")
    return pl.DataFrame(rows) if rows else pl.DataFrame({"trade_id": [], "tag": [], "notes": [], "rule_version": []})


def tag_summary(tags: pl.DataFrame) -> pl.DataFrame:
    if tags.height == 0:
        return pl.DataFrame({"tag": [], "count": []})
    return tags.group_by("tag").agg(pl.len().alias("count")).sort("count", descending=True)
