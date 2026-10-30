from bot.core.market.state import SymbolConfig, SymbolState
from bot.core.models import Bar, InstrumentSpec


def test_equal_entry_and_context_timeframes_update_both_roles():
    spec = InstrumentSpec(symbol="TESTUSDT", base_asset="TEST")
    state = SymbolState(
        spec,
        SymbolConfig(entry_tf_ms=3_600_000, context_tf_ms=3_600_000, higher_tf_ms=14_400_000),
    )
    complete = None
    for minute in range(120):
        ts_open = minute * 60_000
        bar = Bar(
            symbol="TESTUSDT",
            ts_open_ms=ts_open,
            ts_close_ms=ts_open + 60_000,
            open=1.0,
            high=1.001,
            low=0.999,
            close=1.0,
            volume=1.0,
            quote_volume=1.0,
        )
        update = state.on_1m(bar)
        if update.entry_complete and update.context_complete:
            complete = update
            break
    assert complete is not None
    assert complete.entry_bar is not None and complete.entry_bar.duration_ms == 3_600_000
    assert complete.context_bar is not None and complete.context_bar.duration_ms == 3_600_000
    assert state.context_regime is not None
