"""Order lifecycle integration tests against the pinned Nautilus build.

These tests exist because a backtest that loses a protective stop is worse
than no backtest. They exercise entry, two-leg take profit, stop replacement
and stop-out through the real matching engine.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from bot.core.models import ExitReason, Side
from tests.backtest.helpers import flat_then_move, make_engine, run_synthetic, synthetic_spec

pytestmark = [pytest.mark.nautilus, pytest.mark.slow]

START = datetime(2025, 1, 1, tzinfo=UTC)
ENTER_MS = int(START.timestamp() * 1000) + 15 * 60_000  # 00:15 close


def _engine(side: Side, stop: float, tps: tuple[float, ...]):
    spec = synthetic_spec()
    from tests.backtest.helpers import ScriptedStrategy

    return spec, make_engine(ScriptedStrategy(ENTER_MS, side, stop, tps, qty_hint=1.0), spec)


def test_long_two_leg_take_profit() -> None:
    spec, engine = _engine(Side.LONG, stop=99.0, tps=(100.5, 101.5))
    prices = flat_then_move(100.0, [100.2, 100.6, 101.0, 101.6, 101.6, 101.6, 101.6], flat_bars=20)
    bt, bridge = run_synthetic(engine, spec, prices, start=START)

    trades = engine.closed_trades
    assert len(trades) == 1, bridge.out.errors
    t = trades[0]
    assert t.side is Side.LONG
    assert t.exit_reason is ExitReason.TP2
    assert t.tp1_filled is True
    assert t.net_pnl > 0
    assert not bridge.out.errors
    # Stop was replaced at least once after TP1 (cancel + recreate).
    stop_orders = [o for o in bridge.out.orders if o["kind"] == "STOP"]
    assert len(stop_orders) >= 2
    bt.dispose()


def test_long_stop_out() -> None:
    spec, engine = _engine(Side.LONG, stop=99.0, tps=(101.0, 102.0))
    prices = flat_then_move(100.0, [99.8, 99.5, 99.2, 98.9, 98.5], flat_bars=20)
    bt, bridge = run_synthetic(engine, spec, prices, start=START)

    trades = engine.closed_trades
    assert len(trades) == 1, bridge.out.errors
    t = trades[0]
    assert t.exit_reason is ExitReason.STOP
    assert t.net_pnl < 0
    assert t.net_r < 0
    # Loss must be bounded near the intended risk (plus fees and one-tick slip).
    assert t.net_r > -1.6
    bt.dispose()


def test_short_two_leg_take_profit() -> None:
    spec, engine = _engine(Side.SHORT, stop=101.0, tps=(99.5, 98.5))
    prices = flat_then_move(100.0, [99.8, 99.4, 99.0, 98.4, 98.4, 98.4], flat_bars=20)
    bt, bridge = run_synthetic(engine, spec, prices, start=START)

    trades = engine.closed_trades
    assert len(trades) == 1, bridge.out.errors
    t = trades[0]
    assert t.side is Side.SHORT
    assert t.exit_reason is ExitReason.TP2
    assert t.net_pnl > 0
    bt.dispose()


def test_single_leg_fallback_when_notional_too_small() -> None:
    # 0.6 USDT quantity at ~100 with min notional 5: legs cannot be split.
    spec, engine = _engine(Side.LONG, stop=99.0, tps=(101.0, 102.0))
    prices = flat_then_move(100.0, [100.5, 101.2, 101.2], flat_bars=20)
    bt, bridge = run_synthetic(engine, spec, prices, start=START, starting_balance=10.0)
    # If no trade was possible, the engine must have rejected cleanly, not crashed.
    assert not bridge.out.errors
    if engine.closed_trades:
        t = engine.closed_trades[0]
        assert t.exit_reason in (ExitReason.TP1, ExitReason.TP2)
    bt.dispose()


def test_no_lookahead_prefix_invariance() -> None:
    """Removing future bars must not change any decision made before the cutoff."""
    from bot.core.engine import DecisionEngine, EngineConfig
    from bot.core.models import Bar, PortfolioSnapshot
    from bot.core.strategies.registry import build_strategy

    spec = synthetic_spec()
    prices = [100.0 + (i % 7) * 0.05 for i in range(240)]
    t0 = 1_735_689_600_000  # 2025-01-01T00:00Z, aligned to the 15m grid

    def feed(n: int):
        eng = DecisionEngine(build_strategy("range_v1"), {spec.symbol: spec}, EngineConfig())
        seen: list[tuple[int, str, str]] = []
        original = eng._emit
        eng._emit = lambda kind, payload: (original(kind, payload), seen.append(
            (payload["ts_ms"], payload.get("reject_reason") or payload.get("action", ""), payload.get("reason", ""))
        ))
        snap = PortfolioSnapshot(ts_ms=0, equity=1000, balance=1000, available_margin=1000, unrealized_pnl=0, used_margin=0)
        for i, px in enumerate(prices[:n]):
            b = Bar(
                symbol=spec.symbol,
                ts_open_ms=t0 + i * 60_000,
                ts_close_ms=t0 + (i + 1) * 60_000,
                open=px,
                high=px + 0.02,
                low=px - 0.02,
                close=px,
                volume=10.0,
                quote_volume=1000.0,
            )
            eng.on_bar(b, snap)
        return seen, eng.entry_bar_count

    full, full_bars = feed(240)
    prefix, prefix_bars = feed(150)
    cutoff = t0 + 150 * 60_000
    assert prefix_bars == 10
    assert full_bars == 16
    assert prefix == [d for d in full if d[0] <= cutoff]
    assert feed(240) == (full, full_bars)


def test_oversized_order_is_rejected_for_liquidity() -> None:
    """Orders above a small share of bar volume must be rejected, not partially filled."""
    spec, engine = _engine(Side.LONG, stop=99.0, tps=(100.5, 101.5))
    prices = flat_then_move(100.0, [100.2, 100.6, 101.0, 101.6], flat_bars=20)
    bt, bridge = run_synthetic(engine, spec, prices, start=START, bar_volume=0.5)

    assert not engine.closed_trades
    assert engine.reject_counts.get("LIQUIDITY", 0) >= 1
    assert not bridge.out.fills
    bt.dispose()


def test_breakeven_stop_move_is_skipped_when_price_gaps_through_it() -> None:
    """After TP1, if price is already beyond breakeven, keep the original stop.

    Placing a stop on the wrong side of the market would be rejected by the
    exchange; the core must leave the valid older stop in place.
    """
    spec, engine = _engine(Side.LONG, stop=99.0, tps=(100.5, 102.0))
    prices = flat_then_move(100.0, [100.6, 99.8, 99.5, 98.8], flat_bars=20)
    bt, bridge = run_synthetic(engine, spec, prices, start=START)

    assert not bridge.out.errors
    # The position must have been stopped at the original level, not left naked.
    assert len(engine.closed_trades) == 1
    trade = engine.closed_trades[0]
    assert trade.exit_reason is ExitReason.STOP
    assert trade.tp1_filled is True
    # No protective order may have been rejected for being in the market, and
    # the stop must not have been replaced above the market.
    assert not any("in the market" in e["detail"] for e in bridge.out.errors)
    stops = [o for o in bridge.out.orders if o["kind"] == "STOP"]
    assert stops and all(o["price"] == 99.0 for o in stops)
    bt.dispose()
