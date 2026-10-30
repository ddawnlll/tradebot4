"""Instrument metadata from the Binance USDT-M exchange.

Only public endpoints are used. Leverage brackets need a signed request; they
are not required here because the account notional (20 USDT equity x 10x) is
far below tier 1 for every symbol, so tier-1 max leverage is the binding limit.
This assumption is recorded in docs/exchange_gaps.md.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import httpx
import polars as pl

from bot.core.models import InstrumentSpec
from bot.data.storage import Paths

log = logging.getLogger(__name__)

FAPI = "https://fapi.binance.com"
MAKER_FEE = 0.0002  # VIP0 USDT-M, no BNB discount
TAKER_FEE = 0.0005


def fetch_exchange_info(timeout: float = 30.0) -> dict:
    with httpx.Client(timeout=timeout) as client:
        r = client.get(f"{FAPI}/fapi/v1/exchangeInfo")
        r.raise_for_status()
        return r.json()


def parse_instruments(payload: dict, *, maker_fee: float = MAKER_FEE, taker_fee: float = TAKER_FEE) -> list[InstrumentSpec]:
    specs: list[InstrumentSpec] = []
    for s in payload.get("symbols", []):
        if s.get("quoteAsset") != "USDT" or s.get("contractType") != "PERPETUAL":
            continue
        filters = {f["filterType"]: f for f in s.get("filters", [])}
        pf = filters.get("PRICE_FILTER", {})
        lot = filters.get("LOT_SIZE", {})
        mn = filters.get("MIN_NOTIONAL", {})
        tick = float(pf.get("tickSize", "0.01"))
        step = float(lot.get("stepSize", "0.001"))
        specs.append(
            InstrumentSpec(
                symbol=s["symbol"],
                base_asset=s["baseAsset"],
                quote_asset=s["quoteAsset"],
                price_tick=tick,
                qty_step=step,
                min_qty=float(lot.get("minQty", "0.001")),
                max_qty=float(lot.get("maxQty", "1e9")),
                min_notional=float(mn.get("notional", "5")),
                maker_fee=maker_fee,
                taker_fee=taker_fee,
                max_leverage=10.0,
                status=s.get("status", "UNKNOWN"),
                onboard_ms=int(s.get("onboardDate", 0)),
            )
        )
    return specs


def save_instruments(specs: list[InstrumentSpec], paths: Paths | None = None) -> Path:
    paths = paths or Paths()
    paths.ensure()
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    rows = [
        {
            "symbol": s.symbol,
            "base_asset": s.base_asset,
            "quote_asset": s.quote_asset,
            "price_tick": s.price_tick,
            "qty_step": s.qty_step,
            "min_qty": s.min_qty,
            "max_qty": s.max_qty,
            "min_notional": s.min_notional,
            "maker_fee": s.maker_fee,
            "taker_fee": s.taker_fee,
            "max_leverage": s.max_leverage,
            "status": s.status,
            "onboard_ms": s.onboard_ms,
            "snapshot": stamp,
        }
        for s in specs
    ]
    df = pl.DataFrame(rows)
    out = paths.instruments / "instruments.parquet"
    df.write_parquet(out, compression="zstd")
    (paths.instruments / "exchange_info.json").write_text(json.dumps({"snapshot": stamp, "symbols": rows}, indent=2))
    return out


def load_instruments(paths: Paths | None = None) -> dict[str, InstrumentSpec]:
    paths = paths or Paths()
    path = paths.instruments / "instruments.parquet"
    if not path.exists():
        raise FileNotFoundError(f"missing {path}; run `bot data instruments` first")
    df = pl.read_parquet(path)
    out: dict[str, InstrumentSpec] = {}
    for row in df.iter_rows(named=True):
        out[row["symbol"]] = InstrumentSpec(
            symbol=row["symbol"],
            base_asset=row["base_asset"],
            quote_asset=row["quote_asset"],
            price_tick=float(row["price_tick"]),
            qty_step=float(row["qty_step"]),
            min_qty=float(row["min_qty"]),
            max_qty=float(row["max_qty"]),
            min_notional=float(row["min_notional"]),
            maker_fee=float(row["maker_fee"]),
            taker_fee=float(row["taker_fee"]),
            max_leverage=float(row["max_leverage"]),
            status=row["status"],
            onboard_ms=int(row["onboard_ms"]),
        )
    return out


def quantify(value: float, step: float) -> Decimal:
    """Decimal quantization helper for order prices/quantities."""
    return Decimal(str(value)).quantize(Decimal(str(step)))
