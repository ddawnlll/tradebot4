from __future__ import annotations

from datetime import UTC, datetime

from bot.backtest.runner import BacktestConfig, run_backtest
from tests.backtest.helpers import require_market_data


def _ms(y: int, m: int) -> int:
    return int(datetime(y, m, 1, tzinfo=UTC).timestamp() * 1000)


def test_static_target_changes_exit_geometry_without_changing_entries():
    require_market_data()
    base = dict(symbols=["WIFUSDT"], start_ms=_ms(2024, 11), end_ms=_ms(2024, 12), use_funding=False,
                journal=False, enrich=False)
    plain = run_backtest(BacktestConfig(**base))
    target = run_backtest(BacktestConfig(**base, static_target_r=2.0))
    assert plain.trades.height > 0
    assert target.trades.height > 0
    # The entry rule is unchanged; only the exit geometry differs.
    assert set(plain.trades["signal_id"].to_list()) == set(target.trades["signal_id"].to_list())
    # A 2R target must not produce a successful target exit below ~1.8R after costs.
    wins = target.trades.filter(target.trades["exit_reason"].is_in(["TP1", "TP2"]))
    if wins.height:
        assert wins["net_r"].min() > 1.6
    assert (target.trades["tp2_price"].is_null()).all()
