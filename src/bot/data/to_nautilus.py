"""Export canonical parquet bars into a NautilusTrader v2 ParquetDataCatalog.

The catalog is a *derived* backtest artifact, never the source of truth. It is
used for reproducible `BacktestNode` runs and golden regression tests.

Pinned-release gaps: the v2.0.0rc8 catalog has writers for bars, trades,
quotes, book data, mark/index prices and custom data, but no funding-rate
writer/query. Funding is therefore fed directly to the engine (see
docs/nautilus_gaps.md).
"""

from __future__ import annotations

import logging
from pathlib import Path

import polars as pl

from nautilus_trader.model import Bar, BarType
from nautilus_trader.persistence import ParquetDataCatalog

from bot.backtest.instruments import make_instrument, nautilus_id
from bot.core.models import InstrumentSpec, config_hash
from bot.data.loader import calendar_chunks, load_bars_frame
from bot.data.storage import Paths, write_metadata

log = logging.getLogger(__name__)
MS = 1_000_000


def export_symbol(
    paths: Paths,
    spec: InstrumentSpec,
    start_ms: int,
    end_ms: int,
    *,
    venue: str = "BINANCE",
    catalog_path: Path | None = None,
    chunk_days: int = 31,
) -> dict:
    catalog_path = catalog_path or paths.catalog
    catalog_path.mkdir(parents=True, exist_ok=True)
    catalog = ParquetDataCatalog(str(catalog_path))
    instrument = make_instrument(spec, venue)
    catalog.write_instruments([instrument])

    bar_type = BarType.from_str(f"{nautilus_id(spec.symbol, venue)}-1-MINUTE-LAST-EXTERNAL")
    written = 0
    for c_start, c_end in calendar_chunks(start_ms, end_ms, chunk_days):
        df = load_bars_frame(paths, spec.symbol, c_start, c_end)
        if df.height == 0:
            continue
        catalog.write_bars(_bars(df, instrument, bar_type))
        written += df.height
    meta = {
        "symbol": spec.symbol,
        "instrument_id": str(instrument.id),
        "bars": written,
        "start_ms": start_ms,
        "end_ms": end_ms,
        "catalog": str(catalog_path),
    }
    write_metadata(catalog_path / "_export" / f"{spec.symbol}.json", meta)
    return meta


def verify_symbol(paths: Paths, spec: InstrumentSpec, start_ms: int, end_ms: int, *, venue: str = "BINANCE") -> dict:
    catalog = ParquetDataCatalog(str(paths.catalog))
    iid = nautilus_id(spec.symbol, venue)
    bars = catalog.query_bars(identifiers=[iid], start=start_ms * MS, end=end_ms * MS)
    df = load_bars_frame(paths, spec.symbol, start_ms, end_ms)
    first_ok = bool(bars) and bars[0].ts_event // MS == int(df["ts_close_ms"][0])
    last_ok = bool(bars) and bars[-1].ts_event // MS == int(df["ts_close_ms"][-1])
    return {
        "symbol": spec.symbol,
        "catalog_bars": len(bars),
        "parquet_bars": df.height,
        "count_match": len(bars) == df.height,
        "first_timestamp_match": first_ok,
        "last_timestamp_match": last_ok,
        "hash": config_hash({"symbol": spec.symbol, "bars": len(bars), "start": start_ms, "end": end_ms}),
    }


def export_universe(paths: Paths, specs: dict[str, InstrumentSpec], symbols: list[str], start_ms: int, end_ms: int, **kw) -> list[dict]:
    out = []
    for s in symbols:
        if s not in specs:
            log.warning("no instrument spec for %s", s)
            continue
        out.append(export_symbol(paths, specs[s], start_ms, end_ms, **kw))
    return out


def _bars(df: pl.DataFrame, instrument, bar_type: BarType) -> list[Bar]:
    out: list[Bar] = []
    mk_price = instrument.make_price
    mk_qty = instrument.make_qty
    for row in df.iter_rows(named=True):
        ns = int(row["ts_close_ms"]) * MS
        out.append(
            Bar(
                bar_type,
                mk_price(float(row["open"])),
                mk_price(float(row["high"])),
                mk_price(float(row["low"])),
                mk_price(float(row["close"])),
                mk_qty(float(row["volume"])),
                ns,
                ns,
            )
        )
    return out
