from __future__ import annotations

from bot.research.chop_scan import failure_cooldown
from bot.research.exit_scan import TradeCase


def _case(tid: str, side: str, entry: int, exit_: int, net_r: float) -> TradeCase:
    return TradeCase(
        trade_id=tid, signal_id=f"range_v1-X-{entry}", symbol="X", side=side,
        entry_ts_ms=entry, exit_ts_ms=exit_, entry_price=100.0, stop_price=99.0,
        final_stop_price=99.0, tp1_price=101.0, tp2_price=102.0, quantity=1.0,
        tp1_qty=0.5, rest_qty=0.5, risk_usdt=1.0, actual_net_r=net_r,
        actual_exit_reason="STOP", entry_fee=0.0, support=98.0, resistance=103.0, atr=1.0,
    )


def test_failure_cooldown_vetoes_only_same_side_inside_window():
    first = _case("a", "LONG", 0, 60_000, -1.0)
    inside = _case("b", "LONG", 120_000, 180_000, 1.0)
    outside = _case("c", "LONG", 60_000 + 8 * 900_000, 60_000 + 9 * 900_000, 1.0)
    other_side = _case("d", "SHORT", 120_000, 180_000, 1.0)
    result = failure_cooldown([first, inside, outside, other_side])
    assert [c.trade_id for c in result.kept] == ["a", "d", "c"]
    assert result.vetoed[0]["trade_id"] == "b"


def test_failure_cooldown_does_not_use_a_winning_prior():
    first = _case("a", "LONG", 0, 60_000, 0.5)
    second = _case("b", "LONG", 120_000, 180_000, 1.0)
    result = failure_cooldown([first, second])
    assert [c.trade_id for c in result.kept] == ["a", "b"]
    assert result.vetoed == []
