"""Explicit cost model for the Nautilus v2 backtester.

Every cost is defined here and nowhere else:

* maker / taker commission (venue fee model),
* market entry and exit slippage: ``max(2 bps, 1 tick)`` adverse,
* stop-market slippage: 5 bps adverse on top of any gap,
* limit fills: the market must trade at least one tick beyond the limit,
  otherwise the order is not filled (empty synthetic book),
* optional multiplier for stress tests.

The model returns a synthetic L1 book with one level at the adverse price.
Nautilus fills against that level, so the worse price flows through the
account, the position, the trade record and the reconciliation residual.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass

from nautilus_trader.execution import FillModel
from nautilus_trader.model import (
    BookOrder,
    BookType,
    OrderBook,
    OrderSide,
    OrderType,
)


@dataclass(frozen=True, slots=True)
class CostModel:
    maker_fee: float = 0.0002
    taker_fee: float = 0.0005
    market_slippage_bps: float = 2.0
    stop_slippage_bps: float = 5.0
    limit_tick_buffer: int = 1
    prob_fill_on_limit: float = 0.90
    random_seed: int = 42
    commission_multiplier: float = 1.0
    slippage_multiplier: float = 1.0

    @property
    def effective_maker_fee(self) -> float:
        return self.maker_fee * self.commission_multiplier

    @property
    def effective_taker_fee(self) -> float:
        return self.taker_fee * self.commission_multiplier

    @property
    def effective_market_bps(self) -> float:
        return self.market_slippage_bps * self.slippage_multiplier

    @property
    def effective_stop_bps(self) -> float:
        return self.stop_slippage_bps * self.slippage_multiplier


class CostAwareFillModel(FillModel):
    def __init__(self, model: CostModel) -> None:
        self.model = model
        self._rng = random.Random(model.random_seed)

    # Nautilus hooks -----------------------------------------------------
    def is_limit_filled(self) -> bool:
        return self._rng.random() < self.model.prob_fill_on_limit

    def is_slipped(self) -> bool:
        # Slippage is applied explicitly through the synthetic book.
        return False

    def get_orderbook_for_fill_simulation(self, instrument, order, best_bid, best_ask):
        if best_bid is None or best_ask is None:
            return None
        tick = instrument.price_increment.as_double()
        if tick <= 0:
            return None

        order_type = order.order_type
        if order_type in (OrderType.LIMIT, OrderType.STOP_LIMIT):
            price = getattr(order, "price", None)
            if price is None:
                return None
            return self._limit_book_or_none(instrument, order, price.as_double(), tick, best_bid, best_ask)

        if order_type is OrderType.MARKET:
            return self._synthetic_book(
                instrument, order, best_bid, best_ask, self.model.effective_market_bps
            )
        if order_type in (OrderType.STOP_MARKET, OrderType.MARKET_IF_TOUCHED):
            return self._synthetic_book(
                instrument, order, best_bid, best_ask, self.model.effective_stop_bps
            )
        return None

    # Internals ----------------------------------------------------------
    def _limit_book_or_none(self, instrument, order, price: float, tick: float, best_bid, best_ask):
        buffer = tick * self.model.limit_tick_buffer
        if order.side is OrderSide.BUY:
            # A buy limit at P only fills if the market traded at P - tick or lower.
            if best_ask.as_double() > price - buffer + 1e-12:
                return self._empty_book(instrument)
            return None
        # A sell limit at P only fills if the market traded at P + tick or higher.
        if best_bid.as_double() < price + buffer - 1e-12:
            return self._empty_book(instrument)
        return None

    def _synthetic_book(self, instrument, order, best_bid, best_ask, slippage_bps: float):
        tick = instrument.price_increment.as_double()
        if order.side is OrderSide.BUY:
            base = best_ask.as_double()
            px = base * (1.0 + slippage_bps / 10_000.0)
            px = _round_up(px, tick)
            level_side = OrderSide.SELL
        else:
            base = best_bid.as_double()
            px = base * (1.0 - slippage_bps / 10_000.0)
            px = _round_down(px, tick)
            level_side = OrderSide.BUY
        if px <= 0:
            return None
        qty = max(order.quantity.as_double() * 10.0, instrument.min_quantity.as_double())
        book = OrderBook(instrument.id, BookType.L2_MBP)
        book.add(
            BookOrder(level_side, instrument.make_price(px), instrument.make_qty(qty, round_down=False), 1),
            0,
            0,
            0,
        )
        return book

    @staticmethod
    def _empty_book(instrument) -> OrderBook:
        return OrderBook(instrument.id, BookType.L2_MBP)


def _round_up(value: float, tick: float) -> float:
    return math.ceil(value / tick - 1e-9) * tick


def _round_down(value: float, tick: float) -> float:
    return math.floor(value / tick + 1e-9) * tick
