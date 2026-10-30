"""Golden regression: catalog/BacktestNode path must match the low-level engine.

Both paths share the same bridge and decision engine. A divergence means data
loading, ordering or timestamp semantics differ, which would invalidate
cross-run comparisons.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from bot.backtest.runner import BacktestConfig, run_backtest, run_backtest_node
from bot.data.instruments import load_instruments
from bot.data.storage import Paths
from bot.data.to_nautilus import export_symbol
from tests.backtest.helpers import require_market_data

pytestmark = [pytest.mark.nautilus, pytest.mark.slow]

START = int(datetime(2024, 11, 1, tzinfo=UTC).timestamp() * 1000)
END = int(datetime(2024, 12, 1, tzinfo=UTC).timestamp() * 1000)


def test_node_matches_engine(tmp_path) -> None:
    require_market_data()
    paths = Paths()
    specs = load_instruments(paths)
    if "WIFUSDT" not in specs:
        pytest.skip("instrument metadata missing")
    catalog = tmp_path / "catalog"
    export_symbol(paths, specs["WIFUSDT"], START, END, catalog_path=catalog)

    cfg = BacktestConfig(
        symbols=["WIFUSDT"],
        start_ms=START,
        end_ms=END,
        use_funding=False,
        strict_errors=True,
        chunk_days=3,
    )
    low = run_backtest(cfg, out_dir=tmp_path / "low")
    node = run_backtest_node(cfg, catalog_path=catalog)

    assert low.errors == []
    assert node["errors"] == []
    assert low.trades.height == node["trades"].height
    assert low.trades.height > 0, "golden test must exercise at least one trade"
    if low.trades.height:
        a = float(low.trades["net_pnl"].sum())
        b = float(node["trades"]["net_pnl"].sum())
        assert abs(a - b) < 1e-6
        assert list(low.trades["exit_reason"]) == list(node["trades"]["exit_reason"])
