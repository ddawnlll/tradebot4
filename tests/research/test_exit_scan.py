from __future__ import annotations

from bot.research.exit_scan import TradeCase, simulate_arm


def _bar(ts: int, o: float, h: float, low: float, c: float) -> dict:
    return {"ts_open_ms": ts, "ts_close_ms": ts + 60_000, "open": o, "high": h, "low": low, "close": c, "volume": 1.0}


def _case(side: str = "LONG", entry: float = 100.0, stop: float = 99.0, tp1: float = 101.0, tp2: float = 102.0,
          qty: float = 2.0, risk: float = 2.0, atr: float = 1.0, support: float = 98.0, resistance: float = 103.0) -> TradeCase:
    return TradeCase(
        trade_id="t", signal_id="range_v1-X-0", symbol="X", side=side, entry_ts_ms=0, exit_ts_ms=1,
        entry_price=entry, stop_price=stop, final_stop_price=stop, tp1_price=tp1, tp2_price=tp2, quantity=qty,
        tp1_qty=qty / 2, rest_qty=qty / 2, risk_usdt=risk, actual_net_r=-0.5,
        actual_exit_reason="BREAKOUT", entry_fee=0.1, support=support, resistance=resistance, atr=atr,
    )


def test_two_leg_tp1_then_stop_is_not_same_bar_ambiguous():
    bars = [_bar(0, 100, 101.5, 99.5, 101), _bar(60_000, 101, 100.5, 98.5, 99)]
    r = simulate_arm(_case(), bars, [], None, "no_breakout")
    assert r.exit_reason == "STOP"
    assert not r.ambiguous
    assert len(r.legs) == 2
    assert r.legs[0].reason == "TP1"


def test_same_bar_stop_and_target_is_stop_first():
    bars = [_bar(0, 100, 102.5, 98.5, 100)]
    r = simulate_arm(_case(), bars, [], None, "static_2r")
    assert r.ambiguous
    assert r.exit_reason.startswith("STOP")
    assert r.net_r < -0.9


def test_gap_through_stop_fills_at_open():
    bars = [_bar(0, 98.0, 99.0, 97.5, 98.5)]
    r = simulate_arm(_case(), bars, [], None, "stop_only")
    assert r.exit_reason == "STOP_GAP"
    assert r.legs[0].price == 98.0


def test_trailing_stop_updates_only_after_bar_close():
    bars = [_bar(0, 100, 103, 99.5, 102), _bar(60_000, 102, 102, 100.0, 101)]
    r = simulate_arm(_case(), bars, [], None, "chandelier_2.5")
    # Bar 1 must survive the old stop (99); after its close the trail is 100.5,
    # so bar 2's low of 100 stops the trade at 100.5, not before.
    assert r.exit_reason == "STOP"
    assert r.legs[0].price < 100.5


def test_short_side_is_symmetric():
    bars = [_bar(0, 100, 100.5, 97.5, 99)]
    r = simulate_arm(_case(side="SHORT", stop=101.0, tp1=99.0, tp2=98.0, support=97.0, resistance=102.0), bars, [], None, "static_2r")
    assert r.exit_reason == "TARGET"
    assert r.net_r > 0


def test_funding_sign_for_short():
    import polars as pl
    bars = [_bar(0, 100, 100, 99, 99.5)]
    funding = pl.DataFrame([{"symbol": "X", "ts_ms": 30_000, "rate": 0.01}])
    case = _case(side="SHORT", stop=101.0, tp1=99.0, tp2=98.0)
    r = simulate_arm(case, bars, [], funding, "no_tp")
    # Short receives positive funding: funding_pnl is positive.
    assert r.funding_pnl > 0


def test_breakout_arm_exits_on_close_beyond_support():
    bars = [_bar(0, 99, 99.2, 98.6, 97.5)]
    case = _case(stop=98.0, support=98.0, atr=1.0)
    r = simulate_arm(case, bars, [], None, "breakout_0.0")
    assert r.exit_reason == "BREAKOUT"


def test_structural_arm_requires_full_warmup():
    bars = [_bar(0, 100, 100, 99, 99.5)]
    case = _case()
    r = simulate_arm(case, bars, [], None, "structural_24h")
    assert r.status == "NOT_RUN"
    assert "insufficient" in r.note
