"""Error-tag rules are deterministic and versioned."""

from __future__ import annotations

import polars as pl

from bot.journal.tagging import RULE_VERSION, tag_trades


def _row(trade_id: str, **overrides) -> dict:
    row = {
        "trade_id": trade_id,
        "exit_reason": "STOP",
        "net_r": -1.2,
        "mae_r": -1.2,
        "mfe_r": 0.1,
        "bars_held": 5,
        "entry_price": 100.0,
        "quantity": 1.0,
        "slippage": 0.0,
    }
    row.update(overrides)
    return row


def test_tag_rules() -> None:
    df = pl.DataFrame(
        [
            _row("t1"),
            _row("t2", exit_reason="BREAKOUT", net_r=-0.8, mfe_r=0.0),
            _row("t3", exit_reason="TIME_STOP", net_r=0.05, mfe_r=0.1),
            _row("t4", exit_reason="STOP_BREAKEVEN", net_r=0.2, mfe_r=2.0, mae_r=-0.1),
            _row("t5", exit_reason="TP2", net_r=0.8, mfe_r=1.0, slippage=0.1),
        ]
    )
    tags = tag_trades(df)
    by_trade: dict[str, set[str]] = {}
    for row in tags.iter_rows(named=True):
        by_trade.setdefault(row["trade_id"], set()).add(row["tag"])

    assert "SWEEP_STOP" in by_trade["t1"]
    assert {"BREAKOUT_AGAINST", "FALSE_RANGE", "SQUEEZE"} <= by_trade["t2"]
    assert "TIME_STOP_FLAT" in by_trade["t3"]
    assert "EARLY_EXIT" in by_trade["t4"]
    assert "HIGH_SLIPPAGE" in by_trade["t5"]
    assert set(tags["rule_version"]) == {RULE_VERSION}


def test_empty_frame_is_safe() -> None:
    tags = tag_trades(pl.DataFrame({"trade_id": []}))
    assert tags.height == 0
    assert "trade_id" in tags.columns
