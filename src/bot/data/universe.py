"""Point-in-time universe construction.

Survivorship bias is the main risk here. The candidate list is *not* the
current exchange listing: it is the S3 inventory of every USDT-M monthly kline
directory that has ever existed, including delisted symbols. Eligibility is
then evaluated with data available strictly before the decision date:

* symbol had a daily bar on or before ``as_of - min_age_days``,
* symbol traded on the day before ``as_of`` (not delisted yet),
* trailing 24h quote volume rank <= top_n.

The resulting universe is frozen for a research window. Freezing is a
deliberate simplification: a live system would re-rank daily, but re-ranking
inside a backtest adds a second selection effect that is hard to attribute.
The frozen universe is re-ranked for *scoring* (liquidity component) but not
for membership.
"""

from __future__ import annotations

import asyncio
import logging
import re
import urllib.parse
import urllib.request
from datetime import UTC, date, datetime, timedelta

import httpx
import polars as pl

from bot.data.download import Downloader, monthly_file
from bot.data.normalize import normalize_klines_file
from bot.data.storage import Paths

log = logging.getLogger(__name__)

S3 = "https://s3-ap-northeast-1.amazonaws.com/data.binance.vision"
SYMBOL_KEY = "data/futures/um/monthly/klines/"


def list_all_symbols(timeout: float = 60.0, include_non_usdt: bool = False) -> list[str]:
    def page(marker: str) -> tuple[list[str], str | None]:
        q = urllib.parse.urlencode({"delimiter": "/", "prefix": SYMBOL_KEY, "max-keys": 1000, "marker": marker})
        with urllib.request.urlopen(f"{S3}?{q}", timeout=timeout) as r:
            body = r.read().decode()
        found = re.findall(r"<CommonPrefixes><Prefix>" + re.escape(SYMBOL_KEY) + r"([^<]+)/</Prefix>", body)
        truncated = "<IsTruncated>true</IsTruncated>" in body
        return found, (SYMBOL_KEY + found[-1] + "/" if truncated and found else None)

    symbols: list[str] = []
    marker = ""
    while True:
        found, marker = page(marker)
        symbols.extend(found)
        if not marker:
            break
    out = sorted(set(symbols))
    if not include_non_usdt:
        out = [s for s in out if s.endswith("USDT")]
    return out


def build_universe(
    as_of: date,
    top_n: int = 40,
    min_age_days: int = 30,
    *,
    paths: Paths | None = None,
    concurrency: int = 12,
    max_symbols: int | None = None,
    include_non_usdt: bool = False,
) -> pl.DataFrame:
    paths = paths or Paths()
    paths.ensure()
    symbols = list_all_symbols()
    if include_non_usdt:
        symbols = list_all_symbols(include_non_usdt=True)
    if max_symbols:
        symbols = symbols[:max_symbols]
    log.info("universe candidates: %d", len(symbols))

    start = as_of - timedelta(days=min_age_days + 10)
    dl = Downloader(paths=paths, concurrency=concurrency)
    months = _months_between(start, as_of - timedelta(days=1))
    files = [monthly_file("klines", s, "1d", y, m) for s in symbols for (y, m) in months]
    asyncio.run(dl.fetch_many(files))

    rows: list[dict] = []
    for s in symbols:
        fs = [dl.target_path(monthly_file("klines", s, "1d", y, m)) for (y, m) in months]
        fs = [p for p in fs if p.exists()]
        if not fs:
            continue
        try:
            df = pl.concat([normalize_klines_file(p, s) for p in fs], how="vertical_relaxed").sort("ts_open_ms")
        except Exception as exc:  # pragma: no cover
            log.warning("universe parse failed %s: %s", s, exc)
            continue
        if df.height == 0:
            continue
        as_of_ms = int(datetime(as_of.year, as_of.month, as_of.day, tzinfo=UTC).timestamp() * 1000)
        eligible_hist = df.filter(pl.col("ts_open_ms") <= as_of_ms - min_age_days * 86_400_000)
        traded_recently = df.filter(pl.col("ts_close_ms") <= as_of_ms).tail(1)
        if eligible_hist.height == 0 or traded_recently.height == 0:
            continue
        last = traded_recently.row(0, named=True)
        rows.append(
            {
                "symbol": s,
                "as_of": as_of.isoformat(),
                "quote_volume_24h": float(last["quote_volume"]),
                "last_bar_ts_ms": int(last["ts_open_ms"]),
                "first_bar_ts_ms": int(df["ts_open_ms"][0]),
            }
        )

    if not rows:
        return pl.DataFrame()
    out = pl.DataFrame(rows).sort("quote_volume_24h", descending=True)
    out = out.with_columns(
        pl.int_range(1, out.height + 1, eager=True).alias("rank"),
        (pl.int_range(1, out.height + 1, eager=True) <= top_n).alias("in_universe"),
    )
    target = paths.universe / f"universe_{as_of.isoformat()}_top{top_n}.parquet"
    out.write_parquet(target, compression="zstd")
    log.info("universe %s: %d eligible, top %d selected", as_of, out.height, top_n)
    return out


def _months_between(start: date, end: date) -> list[tuple[int, int]]:
    out: list[tuple[int, int]] = []
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month):
        out.append((y, m))
        m += 1
        if m == 13:
            y, m = y + 1, 1
    return out


def load_universe(path) -> pl.DataFrame:
    return pl.read_parquet(path)


def universe_symbols(path) -> list[str]:
    df = pl.read_parquet(path)
    return df.filter(pl.col("in_universe")).sort("rank")["symbol"].to_list()


def fetch_universe_snapshot() -> dict:
    """Lightweight current snapshot (not used for backtests)."""
    with httpx.Client(timeout=30) as client:
        r = client.get("https://fapi.binance.com/fapi/v1/ticker/24hr")
        r.raise_for_status()
        data = r.json()
    rows = [d for d in data if d["symbol"].endswith("USDT")]
    rows.sort(key=lambda d: float(d["quoteVolume"]), reverse=True)
    return {"ts": datetime.now(UTC).isoformat(), "symbols": [d["symbol"] for d in rows]}
