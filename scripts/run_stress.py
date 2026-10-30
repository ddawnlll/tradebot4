"""Baseline cost stress on the full development period."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import polars as pl

from bot.backtest.runner import BacktestConfig, run_backtest
from bot.core.market.scoring import RangeScoreConfig
from bot.data.storage import Paths
from bot.kaizen.experiments import record_experiment

ROOT = Path(__file__).resolve().parents[1]
START = 1730419200000  # 2024-11-01
END = 1767139200000    # 2026-01-01, same period as the validation champion dev


def main() -> None:
    paths = Paths()
    files = sorted(paths.universe.glob("universe_*.parquet"))
    symbols = (
        pl.read_parquet(files[-1]).filter(pl.col("in_universe")).sort("rank")["symbol"].to_list()
    )
    rows = {}
    for label, cm, sm in (("normal", 1.0, 1.0), ("stress", 1.5, 2.0)):
        cfg = BacktestConfig(
            strategy="range_v1",
            strategy_params={},
            range_score=RangeScoreConfig(),
            symbols=symbols,
            start_ms=START,
            end_ms=END,
            commission_multiplier=cm,
            slippage_multiplier=sm,
            use_funding=True,
            require_funding_coverage=True,
            chunk_days=30,
        )
        res = run_backtest(cfg)
        rows[label] = res.metrics
        record_experiment(
            {
                "round": "research_round1",
                "phase": "baseline_stress",
                "hypothesis": "baseline",
                "branch": "eval/cost-fix",
                "data_slice": "development",
                "start_ms": START,
                "end_ms": END,
                "symbols": len(symbols),
                "config_hash": res.metrics.get("config_hash") or "",
                "run_id": res.run_id,
                "params_json": {
                    "strategy": "range_v1",
                    "strategy_params": {},
                    "range_score": "default",
                    "commission_multiplier": cm,
                    "slippage_multiplier": sm,
                },
                "metrics_json": res.metrics,
                "notes": f"cost stress {label}: commission x{cm}, slippage x{sm}",
            }
        )
    out = {
        "generated": datetime.now(UTC).isoformat(),
        "start": "2024-11-01",
        "end": "2025-12-31",
        "symbols": len(symbols),
        "normal": rows["normal"],
        "stress": rows["stress"],
    }
    path = ROOT / "reports" / "baseline_costed" / "stress.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out, indent=2, default=str))
    print(f"wrote {path}")


if __name__ == "__main__":
    main()
