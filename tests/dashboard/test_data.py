from __future__ import annotations

import json
from pathlib import Path

import polars as pl
import pytest

from dashboard import data


def _trade(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "trade_id": "t",
        "signal_id": "s",
        "symbol": "BTCUSDT",
        "side": "LONG",
        "entry_price": 100.0,
        "exit_price": 101.0,
        "quantity": 1.0,
        "entry_ts_ms": 1_700_000_000_000,
        "exit_ts_ms": 1_700_000_060_000,
        "gross_pnl": 1.0,
        "fees": 0.0,
        "funding": 0.0,
        "slippage": 0.0,
        "net_pnl": 1.0,
        "net_r": 1.0,
        "risk_usdt": 1.0,
        "mae_r": 0.0,
        "mfe_r": 1.0,
        "tp1_filled": False,
        "exit_reason": "TP2",
        "strategy_version": "1.0.0",
        "setup_score": 0.6,
        "bars_held": 1,
    }
    base.update(overrides)
    return base


def _write_run(root: Path, run_id: str, created: str, trades: list[dict[str, object]] | None) -> Path:
    path = root / run_id
    path.mkdir(parents=True)
    (path / "metadata.json").write_text(
        json.dumps({"run_id": run_id, "created": created, "config": {"strategy": "range_v1"}})
    )
    (path / "metrics.json").write_text(
        json.dumps({"trades": len(trades or []), "net_pnl": 1.0, "return_pct": 5.0})
    )
    if trades is not None:
        pl.DataFrame(trades, schema=data.TRADE_SCHEMA).write_parquet(path / "trades.parquet")
    return path


# ---------------------------------------------------------------------------
# Run discovery
# ---------------------------------------------------------------------------
def test_list_runs_discovers_and_sorts(tmp_path: Path) -> None:
    _write_run(
        tmp_path,
        "20240101T000000Z_range_v1_aaaaaaaaaaaaaaaa",
        "2024-01-01T00:00:00+00:00",
        [_trade(trade_id="a", net_pnl=2.0, net_r=2.0)],
    )
    _write_run(
        tmp_path,
        "20240201T000000Z_range_v1_bbbbbbbbbbbbbbbb",
        "2024-02-01T00:00:00+00:00",
        [_trade(trade_id="b"), _trade(trade_id="c")],
    )
    _write_run(tmp_path, "20240301T000000Z_range_v1_cccccccccccccccc", "2024-03-01T00:00:00+00:00", None)

    runs = data.list_runs(tmp_path)

    assert runs.height == 3
    assert runs["run_id"][0].startswith("20240301")
    assert runs["has_trades"].to_list() == [False, True, True]
    assert runs["has_trades"].dtype == pl.Boolean
    assert runs["strategy_version"].to_list() == [None, "1.0.0", "1.0.0"]
    assert runs["trades"].to_list() == [0, 2, 1]
    assert runs["source"].unique().to_list() == ["backtest"]


def test_list_runs_missing_directory_returns_empty(tmp_path: Path) -> None:
    runs = data.list_runs(tmp_path / "does-not-exist")

    assert runs.is_empty()
    assert "run_id" in runs.columns
    assert "has_trades" in runs.columns


def test_runs_dir_env_override(tmp_path: Path, monkeypatch) -> None:
    custom = tmp_path / "custom_runs"
    monkeypatch.setenv("TRADEBOT_RUNS_DIR", str(custom))
    monkeypatch.setenv("TRADEBOT_HOME", str(tmp_path))

    assert data.runs_dir() == custom.resolve()


# ---------------------------------------------------------------------------
# Drawdown
# ---------------------------------------------------------------------------
def test_compute_drawdown_peak_and_returns() -> None:
    equity = pl.DataFrame(
        {
            "ts_ms": [3, 1, 2, 4, 5],
            "equity": [90.0, 100.0, 120.0, 110.0, 80.0],
        }
    )

    out = data.compute_drawdown(equity, starting_balance=100.0)

    assert out["ts_ms"].to_list() == [1, 2, 3, 4, 5]
    assert out["peak_equity"].to_list() == [100.0, 120.0, 120.0, 120.0, 120.0]
    assert out["drawdown_usdt"].to_list() == [0.0, 0.0, -30.0, -10.0, -40.0]
    assert out["drawdown_pct"].to_list()[2] == -30.0 / 120.0
    assert out["cum_return_pct"].to_list()[-1] == pytest.approx(-0.2)
    assert out["ret_pct"].to_list()[1] == pytest.approx(0.2)


def test_compute_drawdown_empty_frame_is_safe() -> None:
    empty = pl.DataFrame(schema={"ts_ms": pl.Int64, "equity": pl.Float64})

    out = data.compute_drawdown(empty)

    assert out.is_empty()
    assert "drawdown_pct" in out.columns


# ---------------------------------------------------------------------------
# Calibration
# ---------------------------------------------------------------------------
def test_calibration_by_decile_groups_and_averages() -> None:
    rows = [
        _trade(trade_id=f"l{i}", setup_score=0.50 + i * 0.01, net_r=-1.0, net_pnl=-1.0)
        for i in range(5)
    ]
    rows += [
        _trade(trade_id=f"w{i}", setup_score=0.60 + i * 0.01, net_r=1.0, net_pnl=1.0)
        for i in range(5)
    ]
    trades = pl.DataFrame(rows, schema=data.TRADE_SCHEMA)

    out = data.calibration_by_decile(trades, n_bins=2)

    assert out.height == 2
    assert out["n"].to_list() == [5, 5]
    assert out["mean_net_r"].to_list() == [-1.0, 1.0]
    assert out["win_rate"].to_list() == [0.0, 1.0]
    assert out["ci_low"].to_list()[0] <= out["mean_net_r"].to_list()[0]
    assert out["ci_high"].to_list()[1] >= out["mean_net_r"].to_list()[1]


def test_calibration_handles_missing_scores() -> None:
    trades = pl.DataFrame(
        [_trade(trade_id="a", setup_score=None), _trade(trade_id="b", setup_score=0.7)],
        schema=data.TRADE_SCHEMA,
    )

    out = data.calibration_by_decile(trades, n_bins=10)

    assert out.height == 1
    assert out["n"].to_list() == [1]


def test_score_trend_direction() -> None:
    trades = pl.DataFrame(
        [
            _trade(trade_id=str(i), setup_score=0.5 + i * 0.05, net_r=-1.0 + i * 0.5)
            for i in range(6)
        ],
        schema=data.TRADE_SCHEMA,
    )

    trend = data.score_trend(trades)

    assert trend.height == 1
    assert trend["slope"][0] > 0
    assert trend["r2"][0] > 0.99


# ---------------------------------------------------------------------------
# Error tags
# ---------------------------------------------------------------------------
def test_derive_error_tags_from_trade_fields() -> None:
    trades = pl.DataFrame(
        [
            _trade(
                trade_id="stop",
                exit_reason="STOP",
                net_r=-1.0,
                net_pnl=-1.0,
                mae_r=-1.2,
                mfe_r=0.05,
                fees=0.2,
                risk_usdt=1.0,
            ),
            _trade(trade_id="target", exit_reason="TP2", net_r=2.0, mfe_r=2.0),
            _trade(
                trade_id="gaveback",
                exit_reason="STOP_BREAKEVEN",
                net_r=-0.2,
                net_pnl=-0.2,
                mfe_r=1.2,
                mae_r=-0.1,
            ),
            _trade(
                trade_id="breakout",
                exit_reason="BREAKOUT",
                net_r=-0.8,
                net_pnl=-0.8,
                funding=-0.2,
                risk_usdt=1.0,
                mfe_r=0.3,
            ),
        ],
        schema=data.TRADE_SCHEMA,
    )

    tags = data.derive_error_tags(trades)
    by_trade = {
        trade_id: set(tags.filter(pl.col("trade_id") == trade_id)["tag"].to_list())
        for trade_id in trades["trade_id"].to_list()
    }

    assert {"stop_out", "full_stop_risk", "immediate_adverse", "fee_drag", "large_loss"} <= by_trade["stop"]
    assert by_trade["target"] == set()
    assert {"breakeven_stop", "gave_back_profit"} <= by_trade["gaveback"]
    assert {"breakout_against", "funding_drag"} <= by_trade["breakout"]
    assert tags["source"].unique().to_list() == ["derived"]


def test_tag_summary_frequency_loss_and_improvement() -> None:
    trades = pl.DataFrame(
        [
            _trade(
                trade_id="stop",
                exit_reason="STOP",
                net_r=-1.0,
                net_pnl=-1.0,
                mae_r=-1.1,
                mfe_r=0.1,
            ),
            _trade(
                trade_id="gaveback",
                exit_reason="STOP_BREAKEVEN",
                net_r=-0.2,
                net_pnl=-0.2,
                mfe_r=1.2,
            ),
            _trade(trade_id="clean", exit_reason="TP2", net_r=2.0, net_pnl=2.0, mfe_r=2.0),
        ],
        schema=data.TRADE_SCHEMA,
    )

    summary = data.tag_summary(trades)
    indexed = {row["tag"]: row for row in summary.to_dicts()}

    assert summary.height >= 3
    assert indexed["stop_out"]["frequency"] == 1
    assert indexed["stop_out"]["total_r"] == -1.0
    assert indexed["stop_out"]["improvement_r"] == 1.0
    assert indexed["gave_back_profit"]["improvement_r"] == 1.4
    assert indexed["breakeven_stop"]["frequency"] == 1


# ---------------------------------------------------------------------------
# Breakdowns and execution
# ---------------------------------------------------------------------------
def test_breakdown_by_direction() -> None:
    trades = pl.DataFrame(
        [
            _trade(trade_id="a", side="LONG", net_r=1.0, net_pnl=1.0),
            _trade(trade_id="b", side="LONG", net_r=-1.0, net_pnl=-1.0),
            _trade(trade_id="c", side="SHORT", net_r=2.0, net_pnl=2.0),
        ],
        schema=data.TRADE_SCHEMA,
    )

    out = data.breakdown_by(trades, "direction")
    indexed = {row["key"]: row for row in out.to_dicts()}

    assert indexed["LONG"]["trades"] == 2
    assert indexed["LONG"]["avg_r"] == 0.0
    assert indexed["LONG"]["profit_factor"] == 1.0
    assert indexed["SHORT"]["trades"] == 1


def test_execution_by_trade_marks_adverse_slippage() -> None:
    trades = pl.DataFrame(
        [
            _trade(trade_id="a", signal_id="s1", side="LONG", entry_price=100.0, quantity=2.0),
            _trade(trade_id="b", signal_id="s2", side="SHORT", entry_price=100.0, quantity=1.0),
        ],
        schema=data.TRADE_SCHEMA,
    )
    fills = pl.DataFrame(
        [
            {
                "ts_ms": 1_700_000_000_000,
                "symbol": "BTCUSDT",
                "client_order_id": "c1",
                "kind": "ENTRY",
                "side": "BUY",
                "price": 100.1,
                "qty": 2.0,
                "fee": 0.05,
                "liquidity": "TAKER",
                "order_type": "MARKET",
                "signal_id": "s1",
            },
            {
                "ts_ms": 1_700_000_000_000,
                "symbol": "BTCUSDT",
                "client_order_id": "c2",
                "kind": "ENTRY",
                "side": "SELL",
                "price": 99.9,
                "qty": 1.0,
                "fee": 0.02,
                "liquidity": "TAKER",
                "order_type": "MARKET",
                "signal_id": "s2",
            },
        ],
        schema=data.FILL_SCHEMA,
    )

    out = data.execution_by_trade(trades, fills).sort("trade_id")

    assert out["has_fill"].to_list() == [True, True]
    assert out["slippage_bps"].to_list()[0] > 0
    assert out["slippage_bps"].to_list()[1] > 0
    assert round(out["slippage_usdt"].to_list()[0], 6) == 0.2


# ---------------------------------------------------------------------------
# Hypothetical outcomes
# ---------------------------------------------------------------------------
def _bars(rows: list[tuple[float, float, float, float]]) -> pl.DataFrame:
    base = 1_700_000_000_000
    return pl.DataFrame(
        [
            {
                "symbol": "BTCUSDT",
                "ts_open_ms": base + i * 60_000,
                "ts_close_ms": base + (i + 1) * 60_000,
                "open": open_,
                "high": high,
                "low": low,
                "close": close,
                "volume": 1.0,
                "quote_volume": 100.0,
                "trades": 1,
                "taker_buy_volume": 0.5,
                "taker_buy_quote_volume": 50.0,
            }
            for i, (open_, high, low, close) in enumerate(rows)
        ],
        schema=data.BAR_SCHEMA,
    )


def _rejected_signal() -> dict[str, object]:
    return {
        "ts_ms": 1_700_000_000_000,
        "symbol": "BTCUSDT",
        "action": "REJECT",
        "side": None,
        "support": 99.0,
        "resistance": 103.0,
        "features": json.dumps({"close": 99.8, "atr": 0.5}),
    }


def test_hypothetical_long_target_before_stop() -> None:
    bars = _bars(
        [
            (100.0, 100.8, 99.9, 100.5),
            (100.5, 103.0, 100.4, 102.8),
        ]
    )

    out = data.simulate_hypothetical(_rejected_signal(), bars)

    assert out.height == 1
    row = out.row(0, named=True)
    assert row["side"] == "LONG"
    assert row["valid"] is True
    assert row["outcome"] == "TARGET"
    assert row["r_multiple"] == 2.0
    assert row["bars_held"] == 2
    assert row["stop_price"] == 99.0 - 0.35 * 0.5
    assert row["target_price"] > row["entry_price"]
    assert row["mfe_r"] >= 2.0


def test_hypothetical_stop_hit_first() -> None:
    bars = _bars(
        [
            (100.0, 100.4, 98.5, 98.8),
        ]
    )

    out = data.simulate_hypothetical(_rejected_signal(), bars)

    row = out.row(0, named=True)
    assert row["valid"] is True
    assert row["outcome"] == "STOP"
    assert row["r_multiple"] == -1.0
    assert row["mae_r"] <= -1.0


def test_hypothetical_without_bars_or_levels_is_invalid() -> None:
    signal = {"ts_ms": 1_700_000_000_000, "symbol": "BTCUSDT", "side": "LONG", "features": "{}"}

    no_bars = data.simulate_hypothetical(signal, pl.DataFrame(schema=data.BAR_SCHEMA))
    no_levels = data.simulate_hypothetical(signal, _bars([(100.0, 101.0, 99.0, 100.0)]))

    assert no_bars["valid"].to_list() == [False]
    assert no_bars["outcome"].to_list() == ["NO_DATA"]
    assert no_levels["valid"].to_list() == [False]
    assert "stop" in no_levels["note"][0].lower()


def test_hypothetical_outcomes_respects_limit() -> None:
    signals = pl.DataFrame(
        [
            {
                "ts_ms": 1_700_000_000_000,
                "symbol": "BTCUSDT",
                "action": "REJECT",
                "side": None,
                "score": score,
                "support": 99.0,
                "resistance": 103.0,
                "features": json.dumps({"close": 99.8, "atr": 0.5}),
            }
            for score in (0.9, 0.8, 0.7)
        ],
        schema=data.SIGNAL_SCHEMA,
    )
    bars = _bars([(100.0, 100.5, 99.9, 100.4)])

    out = data.hypothetical_outcomes(
        signals, {"BTCUSDT": bars}, max_signals=2, horizon_bars=1
    )

    assert out.height == 2
    assert out["valid"].all()


# ---------------------------------------------------------------------------
# Data integrity helpers
# ---------------------------------------------------------------------------
def test_setup_for_trade_never_uses_future_signal() -> None:
    signals = pl.DataFrame(
        [
            {
                "ts_ms": 1000,
                "symbol": "BTCUSDT",
                "action": "ENTER",
                "side": "LONG",
                "reason": "ok",
                "reject_reason": None,
                "score": 0.6,
                "regime": "RANGE",
                "regime_confidence": 0.5,
                "support": 99.0,
                "resistance": 103.0,
                "features": "{}",
                "signal_id": "past",
            },
            {
                "ts_ms": 3000,
                "symbol": "BTCUSDT",
                "action": "ENTER",
                "side": "LONG",
                "reason": "ok",
                "reject_reason": None,
                "score": 0.9,
                "regime": "RANGE",
                "regime_confidence": 0.5,
                "support": 90.0,
                "resistance": 110.0,
                "features": "{}",
                "signal_id": "future",
            },
        ],
        schema={**data.SIGNAL_SCHEMA, "signal_id": pl.Utf8},
    )

    setup = data.setup_for_trade(
        signals, signal_id="past", symbol="BTCUSDT", entry_ts_ms=2000
    )

    assert setup.height == 1
    assert setup["signal_id"].to_list() == ["past"]
    assert setup["level_source"].to_list() == ["recorded_signal"]
    assert setup["support"].to_list() == [99.0]


def test_resample_bars_15m() -> None:
    bars = _bars([(100.0 + i, 101.0 + i, 99.0 + i, 100.5 + i) for i in range(30)])
    aligned = pl.int_range(0, pl.len(), dtype=pl.Int64) * 60_000 + 1_730_419_200_000
    bars = bars.with_columns(
        aligned.alias("ts_open_ms"),
        (aligned + 60_000).alias("ts_close_ms"),
    )

    out = data.resample_bars(bars, "15m")

    assert out.height == 2
    assert out["open"].to_list()[0] == 100.0
    assert out["close"].to_list()[0] == 100.5 + 14
    assert out["high"].to_list()[0] == 101.0 + 14
    assert out["volume"].sum() == 30
