from bot.core.engine import DecisionEngine, EngineConfig, ManagedPosition
from bot.core.market.state import SymbolConfig
from bot.core.models import Bar, InstrumentSpec, Side
from bot.core.strategies.range_v1 import RangeV1Strategy


def _bar(symbol: str, ts_ms: int) -> Bar:
    return Bar(symbol=symbol, ts_open_ms=ts_ms - 60_000, ts_close_ms=ts_ms, open=1.0, high=1.01, low=0.99, close=1.0, volume=1.0, quote_volume=1.0)


def test_resting_entry_is_cancelled_after_the_deadline():
    symbol = "TESTUSDT"
    spec = InstrumentSpec(symbol=symbol, base_asset="TEST")
    engine = DecisionEngine(
        RangeV1Strategy(),
        {symbol: spec},
        EngineConfig(symbol=SymbolConfig(entry_tf_ms=900_000)),
    )
    engine.positions[symbol] = ManagedPosition(
        signal_id="sig-1",
        symbol=symbol,
        side=Side.LONG,
        qty=1.0,
        initial_qty=1.0,
        entry_price=0.96,
        stop_price=0.95,
        tp1_price=1.0,
        tp2_price=1.03,
        opened_ts_ms=0,
        risk_usdt=1.0,
        strategy_version="1.0.0",
        is_open=False,
        entry_deadline_ts=1_000_000,
    )
    emitted: list[tuple[str, dict]] = []
    engine._emit = lambda kind, payload: emitted.append((kind, payload))
    intents = engine.on_bar(_bar(symbol, 2_000_000))
    assert len(intents) == 1
    assert intents[0].action == "CANCEL_ENTRY"
    assert engine.positions[symbol].cancel_pending is True
    assert "CANCEL" in [payload.get("action") for kind, payload in emitted if kind == "signal"]
    engine.on_entry_rejected(symbol)
    assert symbol not in engine.positions
