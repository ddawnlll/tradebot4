"""Build NautilusTrader v2 instruments from core :class:`InstrumentSpec`."""

from __future__ import annotations

from decimal import Decimal

from nautilus_trader.model import (
    CryptoPerpetual,
    Currency,
    InstrumentId,
    Money,
    Price,
    Quantity,
    Symbol,
)

from bot.core.models import InstrumentSpec

VENUE = "BINANCE"


def make_instrument(spec: InstrumentSpec, venue: str = VENUE) -> CryptoPerpetual:
    quote = Currency.from_str(spec.quote_asset)
    base = Currency.from_str(spec.base_asset)
    return CryptoPerpetual(
        instrument_id=InstrumentId.from_str(f"{spec.symbol}-PERP.{venue}"),
        raw_symbol=Symbol(spec.symbol),
        base_currency=base,
        quote_currency=quote,
        settlement_currency=quote,
        is_inverse=False,
        price_precision=spec.price_precision,
        size_precision=spec.qty_precision,
        price_increment=Price.from_str(_fmt(spec.price_tick)),
        size_increment=Quantity.from_str(_fmt(spec.qty_step)),
        ts_event=0,
        ts_init=0,
        multiplier=Quantity.from_int(1),
        lot_size=Quantity.from_str(_fmt(spec.qty_step)),
        max_quantity=Quantity.from_str(_fmt(spec.max_qty)),
        min_quantity=Quantity.from_str(_fmt(spec.min_qty)),
        max_notional=None,
        min_notional=Money.from_str(f"{_fmt(spec.min_notional)} {spec.quote_asset}"),
        margin_init=Decimal("1") / Decimal(str(spec.max_leverage)),
        margin_maint=Decimal("0.005"),
    )


def nautilus_id(symbol: str, venue: str = VENUE) -> str:
    return f"{symbol}-PERP.{venue}"


def _fmt(x: float) -> str:
    text = f"{x:.12f}".rstrip("0").rstrip(".")
    return text if text else "0"
