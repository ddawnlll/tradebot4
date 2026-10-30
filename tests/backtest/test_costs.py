"""Explicit cost model: slippage, limit conservatism and fee accounting."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from nautilus_trader.model import OrderSide, OrderType, Price, Quantity

from bot.backtest.fill_models import CostAwareFillModel, CostModel
from bot.backtest.instruments import make_instrument
from bot.backtest.runner import BacktestConfig, run_backtest
from bot.core.models import InstrumentSpec

SPEC = InstrumentSpec(
    symbol="WIFUSDT",
    base_asset="WIF",
    price_tick=0.0001,
    qty_step=1.0,
    min_qty=1.0,
    min_notional=5.0,
)
INST = make_instrument(SPEC)


def _order(order_type: OrderType, side: OrderSide, price: str | None = None, qty: str = "10") -> SimpleNamespace:
    return SimpleNamespace(
        order_type=order_type,
        side=side,
        price=Price.from_str(price) if price is not None else None,
        quantity=Quantity.from_str(qty),
    )


def test_market_slippage_is_at_least_two_bps() -> None:
    model = CostAwareFillModel(CostModel())
    book = model.get_orderbook_for_fill_simulation(
        INST, _order(OrderType.MARKET, OrderSide.BUY), Price.from_str("100"), Price.from_str("100")
    )
    assert book is not None
    assert book.best_ask_price().as_double() == pytest.approx(100.02, abs=1e-9)

    book = model.get_orderbook_for_fill_simulation(
        INST, _order(OrderType.MARKET, OrderSide.SELL), Price.from_str("100"), Price.from_str("100")
    )
    assert book is not None
    assert book.best_bid_price().as_double() == pytest.approx(99.98, abs=1e-9)


def test_stop_slippage_is_five_bps() -> None:
    model = CostAwareFillModel(CostModel())
    book = model.get_orderbook_for_fill_simulation(
        INST, _order(OrderType.STOP_MARKET, OrderSide.SELL), Price.from_str("99"), Price.from_str("99")
    )
    assert book is not None
    assert book.best_bid_price().as_double() == pytest.approx(98.9505, abs=1e-9)


def test_limit_requires_one_tick_through() -> None:
    model = CostAwareFillModel(CostModel())
    # Sell limit touched exactly: no fill.
    book = model.get_orderbook_for_fill_simulation(
        INST, _order(OrderType.LIMIT, OrderSide.SELL, "100.5"), Price.from_str("100.5"), Price.from_str("100.5001")
    )
    assert book is not None
    assert book.best_bid_price() is None
    # Sell limit with one tick through: standard fill path.
    assert (
        model.get_orderbook_for_fill_simulation(
            INST, _order(OrderType.LIMIT, OrderSide.SELL, "100.5"), Price.from_str("100.5001"), Price.from_str("100.5002")
        )
        is None
    )
    # Buy limit touched exactly: no fill.
    book = model.get_orderbook_for_fill_simulation(
        INST, _order(OrderType.LIMIT, OrderSide.BUY, "100.5"), Price.from_str("100.4999"), Price.from_str("100.5")
    )
    assert book is not None
    assert book.best_ask_price() is None


def test_cost_model_changes_fills_fees_and_reconciles() -> None:
    start = int(datetime(2024, 11, 1, tzinfo=UTC).timestamp() * 1000)
    end = int(datetime(2024, 12, 1, tzinfo=UTC).timestamp() * 1000)
    result = run_backtest(
        BacktestConfig(
            symbols=["WIFUSDT"],
            start_ms=start,
            end_ms=end,
            use_funding=False,
            journal=False,
            enrich=False,
        )
    )
    assert result.trades.height > 0
    entries = result.fills.filter(result.fills["kind"] == "ENTRY")
    assert entries.height > 0
    assert entries["cost_bps"].min() >= 1.9
    stops = result.fills.filter(result.fills["kind"] == "STOP")
    if stops.height:
        assert stops["cost_bps"].min() > 5.0  # 5 bps plus any bar gap
    for row in result.fills.iter_rows(named=True):
        rate = 0.0005 if row["liquidity"] == "TAKER" else 0.0002
        assert row["fee"] == pytest.approx(row["price"] * row["qty"] * rate, rel=1e-6, abs=1e-8)
    assert abs(result.metrics["equity_residual_usdt"]) < 1e-6
