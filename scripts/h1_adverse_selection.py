"""Adverse selection: filled passive entries versus cancelled ones."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import polars as pl

from bot.backtest.statistics import bootstrap_mean_ci
from bot.data.loader import load_bars_frame
from bot.data.storage import Paths

ROOT = Path(__file__).resolve().parents[1]
SUMMARY = ROOT / "reports" / "research_round1" / "summary.json"
OUT = ROOT / "reports" / "research_round1" / "h1_adverse_selection.json"
HORIZON_MS = 8 * 3_600_000


def _forward(paths: Paths, symbol: str, ts_ms: int, close: float, side: str) -> float | None:
    df = load_bars_frame(paths, symbol, ts_ms, ts_ms + HORIZON_MS)
    if df.height == 0:
        return None
    sign = 1.0 if side == "LONG" else -1.0
    return (float(df["close"][-1]) / close - 1.0) * sign


def _diff_ci(a: np.ndarray, b: np.ndarray, n_boot: int = 5000, seed: int = 11) -> list[float]:
    if len(a) == 0 or len(b) == 0:
        return [None, None]
    rng = np.random.default_rng(seed)
    ma = rng.choice(a, size=(n_boot, len(a)), replace=True).mean(axis=1)
    mb = rng.choice(b, size=(n_boot, len(b)), replace=True).mean(axis=1)
    d = ma - mb
    return [float(np.quantile(d, 0.05)), float(np.quantile(d, 0.95))]


def main() -> None:
    summary = json.loads(SUMMARY.read_text())
    block = summary["hypotheses"]["H1"]
    best = block["best"]
    paths = Paths()
    rows = {"filled": [], "cancelled": []}
    for row in block["rows"]:
        if row.get("id") != best or not row.get("run_dir"):
            continue
        run = Path(row["run_dir"])
        sig_path = run / "signals"
        if not sig_path.exists():
            continue
        signals = pl.read_parquet(sig_path / "**" / "*.parquet")
        orders = pl.read_parquet(run / "orders.parquet") if (run / "orders.parquet").exists() else pl.DataFrame()
        fills = pl.read_parquet(run / "fills.parquet") if (run / "fills.parquet").exists() else pl.DataFrame()
        filled_keys = set()
        if orders.height and fills.height:
            filled_cids = set(fills.filter(pl.col("kind") == "ENTRY")["client_order_id"].to_list())
            filled_keys = {
                (r["symbol"], int(r["ts_ms"]))
                for r in orders.filter(
                    (pl.col("kind") == "ENTRY") & pl.col("client_order_id").is_in(list(filled_cids))
                ).iter_rows(named=True)
            }
        entry = signals.filter(pl.col("action") == "ENTER")
        is_filled = pl.Series(
            "is_filled",
            [(r["symbol"], int(r["ts_ms"])) in filled_keys for r in entry.iter_rows(named=True)],
        )
        entry = entry.with_columns(is_filled)
        for label, df in (
            ("filled", entry.filter(pl.col("is_filled"))),
            ("cancelled", signals.filter(pl.col("action") == "CANCEL")),
        ):
            for r in df.iter_rows(named=True):
                feats = json.loads(r["features"]) if isinstance(r["features"], str) else (r["features"] or {})
                close = feats.get("close")
                entry = feats.get("entry_price")
                if not close or not entry:
                    continue
                fwd_close = _forward(paths, r["symbol"], int(r["ts_ms"]), float(close), r["side"])
                fwd_entry = _forward(paths, r["symbol"], int(r["ts_ms"]), float(entry), r["side"])
                if fwd_close is None:
                    continue
                rows[label].append({"symbol": r["symbol"], "side": r["side"], "fwd_close": fwd_close,
                                    "fwd_entry": fwd_entry})
    result = {}
    for label, values in rows.items():
        a = np.array([v["fwd_close"] for v in values], dtype=float)
        b = np.array([v["fwd_entry"] for v in values if v["fwd_entry"] is not None], dtype=float)
        mean, lo, hi = bootstrap_mean_ci(a, alpha=0.10) if len(a) else (None, None, None)
        mean_e, lo_e, hi_e = bootstrap_mean_ci(b, alpha=0.10) if len(b) else (None, None, None)
        result[label] = {"n": len(values), "mean_fwd_close": mean, "ci90_fwd_close": [lo, hi],
                         "mean_fwd_entry": mean_e, "ci90_fwd_entry": [lo_e, hi_e]}
    a = np.array([v["fwd_close"] for v in rows["filled"]], dtype=float)
    b = np.array([v["fwd_close"] for v in rows["cancelled"]], dtype=float)
    result["filled_minus_cancelled_fwd_close"] = {
        "mean": float(a.mean() - b.mean()) if len(a) and len(b) else None,
        "ci90": _diff_ci(a, b),
    }
    OUT.write_text(json.dumps({**result, "config": best, "horizon_hours": 8}, indent=2, default=str))
    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
