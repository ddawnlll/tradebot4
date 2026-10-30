"""Diagnostic evaluation of v8-core chop/no-trade filter candidates.

This is a conditional filter screen over the baseline trade set. It does not
re-simulate entries, overlap, equity or the kill switch, so a reduced trade set
with a better mean R is not evidence of a portfolio improvement.

A1 (expected gross excursion vs friction) is DATA_BLOCKED: the only excursion
available here is the future MFE, which cannot be used at entry. A2 (failure
cooldown) and A3 (volatility-compression ratio) are causal and are evaluated
separately; A5 is A2+A3, not the full v8 composite.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import polars as pl

from bot.core.indicators.atr import ATR
from bot.core.models import Bar
from bot.data.loader import load_bars_frame
from bot.data.storage import Paths
from bot.research.exit_scan import TradeCase, _summary, load_cases

MS_15M = 900_000


@dataclass(slots=True)
class FilterResult:
    name: str
    status: str
    reason: str
    kept: list[TradeCase]
    vetoed: list[dict[str, Any]]
    unknown: int = 0


def _bars_15m(paths: Paths, symbol: str, start_ms: int, end_ms: int) -> list[Bar]:
    df = load_bars_frame(paths, symbol, start_ms, end_ms)
    if df.height == 0:
        return []
    df = df.with_columns(((pl.col("ts_open_ms") // MS_15M) * MS_15M).alias("bucket"))
    grouped = (
        df.group_by("bucket")
        .agg(
            pl.col("open").first().alias("open"),
            pl.col("high").max().alias("high"),
            pl.col("low").min().alias("low"),
            pl.col("close").last().alias("close"),
            pl.col("volume").sum().alias("volume"),
            pl.col("quote_volume").sum().alias("quote_volume"),
        )
        .sort("bucket")
    )
    grouped = grouped.filter(pl.col("bucket") + MS_15M <= end_ms)
    return [
        Bar(symbol=symbol, ts_open_ms=int(r["bucket"]), ts_close_ms=int(r["bucket"]) + MS_15M,
            open=float(r["open"]), high=float(r["high"]), low=float(r["low"]),
            close=float(r["close"]), volume=float(r["volume"]), quote_volume=float(r["quote_volume"]))
        for r in grouped.iter_rows(named=True)
    ]


def compression_ratio(paths: Paths, case: TradeCase, lookback: int = 50) -> float | None:
    """ATR14 now divided by the median ATR14 over the prior `lookback` 15m bars.

    All bars close at or before the entry timestamp; the current bar is the last
    closed bar before entry. A ratio < 1 means compressed relative to its own
    recent history. This is a candidate definition, not the v8 0.65 optimum.
    """
    start = case.entry_ts_ms - (lookback + 40) * MS_15M
    bars = _bars_15m(paths, case.symbol, start, case.entry_ts_ms)
    if len(bars) < 20:
        return None
    atr = ATR(14)
    values: list[float] = []
    for bar in bars:
        v = atr.update(bar)
        if v is not None:
            values.append(v)
    if not values:
        return None
    current = values[-1]
    history = values[-lookback - 1:-1]
    if not history or current <= 0:
        return None
    hist_sorted = sorted(history)
    med = hist_sorted[len(hist_sorted) // 2]
    if med <= 0:
        return None
    return current / med


def failure_cooldown(trades: list[TradeCase], bars: int = 8) -> FilterResult:
    kept: list[TradeCase] = []
    vetoed: list[dict[str, Any]] = []
    prior: dict[tuple[str, str], TradeCase] = {}
    for case in sorted(trades, key=lambda c: c.entry_ts_ms):
        last = prior.get((case.symbol, case.side))
        if (last is not None and last.exit_ts_ms <= case.entry_ts_ms and last.actual_net_r < 0
                and case.entry_ts_ms - last.exit_ts_ms < bars * MS_15M):
            vetoed.append({"trade_id": case.trade_id, "reason": "A2_COOLDOWN",
                           "last_trade": last.trade_id, "gap_ms": case.entry_ts_ms - last.exit_ts_ms})
        else:
            kept.append(case)
            prior[(case.symbol, case.side)] = case
    return FilterResult("A2_cooldown", "EVALUATED", "8 bars after a failed same-side campaign", kept, vetoed)


def compression_filter(paths: Paths, trades: list[TradeCase], threshold: float = 0.65) -> FilterResult:
    kept: list[TradeCase] = []
    vetoed: list[dict[str, Any]] = []
    unknown = 0
    for case in trades:
        ratio = compression_ratio(paths, case)
        if ratio is None:
            unknown += 1
            vetoed.append({"trade_id": case.trade_id, "reason": "A3_UNKNOWN", "ratio": None})
        elif ratio < threshold:
            vetoed.append({"trade_id": case.trade_id, "reason": "A3_COMPRESSED", "ratio": ratio})
        else:
            kept.append(case)
    return FilterResult("A3_compression", "EVALUATED", f"ATR14 / median ATR14({50}) >= {threshold}",
                        kept, vetoed, unknown)


def score(cases: list[TradeCase]) -> dict[str, Any]:
    return _summary([c.actual_net_r for c in cases])


def scan(run_dir: str | Path, paths: Paths | None = None) -> dict[str, Any]:
    paths = paths or Paths()
    cases, meta = load_cases(run_dir)
    a2 = failure_cooldown(cases)
    a3 = compression_filter(paths, cases)
    a2_kept = set(c.trade_id for c in a2.kept)
    a3_kept = set(c.trade_id for c in a3.kept)
    a5_kept = [c for c in cases if c.trade_id in a2_kept and c.trade_id in a3_kept]
    out = {
        "source_run_dir": str(run_dir),
        "source_run_id": meta.get("run_id"),
        "source_config_hash": meta.get("config_hash"),
        "diagnostic_only": True,
        "conditional_on_baseline_trade_set": True,
        "portfolio_equity_claimed": False,
        "A1": {"status": "DATA_BLOCKED",
               "reason": "expected gross excursion must be a causal, fold-safe estimate; the only excursion available is future MFE, which cannot be used at entry"},
        "baseline": {"n": len(cases), **score(cases)},
        "A2": {"n": len(a2.kept), "vetoed": len(a2.vetoed), **score(a2.kept),
               "veto_examples": a2.vetoed[:10]},
        "A3": {"n": len(a3.kept), "vetoed": len([v for v in a3.vetoed if v["reason"] == "A3_COMPRESSED"]),
               "unknown": a3.unknown, **score(a3.kept), "veto_examples": a3.vetoed[:10]},
        "A5_A2_A3": {"n": len(a5_kept), **score(a5_kept),
                     "note": "A2+A3 only; A1 is DATA_BLOCKED, so this is not the full v8 A5 composite"},
    }
    for key in ("A2", "A3", "A5_A2_A3"):
        base = out["baseline"].get("mean_r")
        val = out[key].get("mean_r")
        out[key]["delta_mean_r"] = (val - base) if (base is not None and val is not None) else None
    return out


def render_markdown(result: dict[str, Any]) -> str:
    lines = [
        "# Chop / no-trade diagnostic",
        "",
        f"Source: `{result['source_run_dir']}` (run `{result.get('source_run_id')}`).",
        "**Diagnostic only.** The baseline trade set is filtered conditionally; entries, equity, overlap and the kill switch are not re-simulated.",
        "",
        "| filter | n | sum R | mean R | PF | win | delta mean R | vetoed | unknown |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for key in ("baseline", "A2", "A3", "A5_A2_A3"):
        a = result[key]
        lines.append(
            f"| {key} | {a['n']} | {a.get('sum_r', 0):+.3f} | {a.get('mean_r', 0):+.4f} | "
            f"{a.get('pf') if a.get('pf') is not None else '-'} | {a.get('win_rate', 0):.3f} | "
            f"{a.get('delta_mean_r', 0) if a.get('delta_mean_r') is not None else 0:+.4f} | "
            f"{a.get('vetoed', 0)} | {a.get('unknown', 0)} |"
        )
    lines += ["", f"* A1: `{result['A1']['status']}` — {result['A1']['reason']}", "",
              "* A2: " + result["A2"].get("reason", ""), "* A3: " + result["A3"].get("reason", ""),
              "* A5 is A2+A3 only; it is not the full v8 composite.", "",
              "A filter that leaves few trades or improves mean R here is a candidate for a full replay, not evidence of profit.", ""]
    return "\n".join(lines)
