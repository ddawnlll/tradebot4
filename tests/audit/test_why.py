from __future__ import annotations

import polars as pl
import pytest

from bot.audit.why import append_history, audit_run, render_markdown


def _run_dir(tmp_path):
    trades = pl.DataFrame(
        [
            {"symbol": "AAA", "side": "LONG", "entry_ts_ms": 1_000, "exit_ts_ms": 2_000,
             "gross_pnl": 1.0, "fees": 0.05, "funding": 0.0, "net_pnl": 0.95, "net_r": 0.95,
             "mae_r": -0.2, "mfe_r": 1.0, "exit_reason": "TP1", "bars_held": 10},
            {"symbol": "AAA", "side": "LONG", "entry_ts_ms": 3_000, "exit_ts_ms": 4_000,
             "gross_pnl": -2.0, "fees": 0.05, "funding": 0.0, "net_pnl": -2.05, "net_r": -1.05,
             "mae_r": -0.9, "mfe_r": 0.1, "exit_reason": "BREAKOUT", "bars_held": 5},
            {"symbol": "BBB", "side": "SHORT", "entry_ts_ms": 5_000, "exit_ts_ms": 6_000,
             "gross_pnl": 0.5, "fees": 0.05, "funding": -0.01, "net_pnl": 0.46, "net_r": 0.46,
             "mae_r": -0.1, "mfe_r": 0.5, "exit_reason": "STOP_BREAKEVEN", "bars_held": 8},
        ]
    )
    fills = pl.DataFrame(
        [
            {"side": "BUY", "price": 101.0, "reference_price": 100.0, "qty": 1.0},
            {"side": "SELL", "price": 99.0, "reference_price": 100.0, "qty": 1.0},
        ]
    )
    trades.write_parquet(tmp_path / "trades.parquet")
    fills.write_parquet(tmp_path / "fills.parquet")
    return tmp_path


def test_audit_decomposes_costs_and_exits(tmp_path):
    run = _run_dir(tmp_path)
    audit = audit_run(run, starting_balance=20.0, metric={"profit_factor": 0.5, "expectancy_r": -0.2, "win_rate": 0.66,
                                                         "max_drawdown_pct": 55.0, "final_equity": 9.0, "strategy": "test"})
    assert audit["trades"] == 3
    assert audit["gross"] == -0.5
    assert audit["slippage"] == pytest.approx(2.0)
    assert audit["gross_before_slippage"] == pytest.approx(1.5)
    assert audit["fees"] == pytest.approx(0.15)
    assert audit["top_loss_reason"] == "BREAKOUT"
    assert audit["top_loss_share"] == 1.0
    assert audit["risk"]["kill_switch"] is True
    assert audit["mae_mfe"]["realized_le_1r"] == 1
    assert any(c["cause"] == "Exit realization channel: BREAKOUT" for c in audit["root_causes"])


def test_history_is_append_once(tmp_path):
    run = _run_dir(tmp_path)
    audit = audit_run(run, metric={"profit_factor": 0.5, "expectancy_r": -0.2, "win_rate": 0.66,
                                   "max_drawdown_pct": 55.0, "final_equity": 9.0, "strategy": "test"})
    path = tmp_path / "history.parquet"
    append_history(audit, path, "test", "NO_GO")
    append_history(audit, path, "test", "NO_GO")
    df = pl.read_parquet(path)
    assert df.height == 1
    assert df["run_id"][0] == run.name


def test_markdown_contains_the_audit_sections(tmp_path):
    run = _run_dir(tmp_path)
    audit = audit_run(run, metric={"profit_factor": 0.5, "expectancy_r": -0.2, "win_rate": 0.66,
                                   "max_drawdown_pct": 55.0, "final_equity": 9.0, "strategy": "test"})
    md = render_markdown(audit, None, verdict="NO_GO")
    assert "Cost waterfall" in md
    assert "Root causes" in md
    assert "Verdict: NO_GO" in md
    assert "BREAKOUT" in md
