"""Binance public data downloader (data.binance.vision).

Design: idempotent, resumable, checksum-verified, bounded concurrency. Nothing
here talks to a trading endpoint and no API key is needed.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import httpx

from bot.data.storage import Paths

log = logging.getLogger(__name__)

BASE = "https://data.binance.vision"
MARKET = "futures/um"


@dataclass(frozen=True, slots=True)
class RemoteFile:
    kind: str  # klines | fundingRate | markPriceKlines
    symbol: str
    interval: str
    period: str  # monthly | daily
    stamp: str  # 2025-01 or 2025-01-31
    filename: str

    @property
    def url(self) -> str:
        if self.kind == "fundingRate":
            return f"{BASE}/data/{MARKET}/{self.period}/{self.kind}/{self.symbol}/{self.filename}"
        return f"{BASE}/data/{MARKET}/{self.period}/{self.kind}/{self.symbol}/{self.interval}/{self.filename}"


def monthly_file(kind: str, symbol: str, interval: str, year: int, month: int) -> RemoteFile:
    stamp = f"{year:04d}-{month:02d}"
    if kind == "fundingRate":
        name = f"{symbol}-fundingRate-{stamp}.zip"
        return RemoteFile(kind, symbol, interval or "", "monthly", stamp, name)
    name = f"{symbol}-{interval}-{stamp}.zip"
    return RemoteFile(kind, symbol, interval, "monthly", stamp, name)


def daily_file(kind: str, symbol: str, interval: str, day: date) -> RemoteFile:
    stamp = day.isoformat()
    if kind == "fundingRate":
        name = f"{symbol}-fundingRate-{stamp}.zip"
        return RemoteFile(kind, symbol, interval or "", "daily", stamp, name)
    name = f"{symbol}-{interval}-{stamp}.zip"
    return RemoteFile(kind, symbol, interval, "daily", stamp, name)


def iter_files(kind: str, symbol: str, interval: str, start: date, end: date) -> list[RemoteFile]:
    """Monthly files for complete months, daily files for the current month."""
    out: list[RemoteFile] = []
    today = datetime.now(UTC).date()
    cur = start
    # monthly chunks
    while cur <= end:
        if cur.day == 1 and _month_end(cur) <= min(end, today - timedelta(days=1)):
            out.append(monthly_file(kind, symbol, interval, cur.year, cur.month))
            cur = _month_end(cur) + timedelta(days=1)
        else:
            out.append(daily_file(kind, symbol, interval, cur))
            cur += timedelta(days=1)
    return out


def _month_end(d: date) -> date:
    if d.month == 12:
        return date(d.year, 12, 31)
    return date(d.year, d.month + 1, 1) - timedelta(days=1)


class Downloader:
    def __init__(self, paths: Paths | None = None, concurrency: int = 8, retries: int = 5, timeout: float = 60.0) -> None:
        self.paths = paths or Paths()
        self.concurrency = concurrency
        self.retries = retries
        self.timeout = timeout
        self._sem = asyncio.Semaphore(concurrency)
        self.stats = {"downloaded": 0, "skipped": 0, "missing": 0, "failed": 0, "bytes": 0}

    def target_path(self, f: RemoteFile) -> Path:
        if f.kind == "klines":
            sub = f"klines/{f.interval}"
        elif f.kind == "markPriceKlines":
            sub = f"markPriceKlines/{f.interval}"
        else:
            sub = "fundingRate"
        return self.paths.raw / sub / f.symbol / f.filename

    async def fetch_one(self, client: httpx.AsyncClient, f: RemoteFile, *, force: bool = False) -> Path | None:
        target = self.target_path(f)
        target.parent.mkdir(parents=True, exist_ok=True)
        checksum_file = target.with_suffix(target.suffix + ".CHECKSUM")
        async with self._sem:
            if target.exists() and checksum_file.exists() and not force:
                if self._verify(target, checksum_file.read_text()):
                    self.stats["skipped"] += 1
                    return target
            for attempt in range(self.retries):
                try:
                    r = await client.get(f.url, timeout=self.timeout)
                    if r.status_code == 404:
                        self.stats["missing"] += 1
                        return None
                    r.raise_for_status()
                    data = r.content
                    if len(data) < 100:
                        self.stats["missing"] += 1
                        return None
                    target.write_bytes(data)
                    self.stats["bytes"] += len(data)
                    # checksum
                    c = await client.get(f.url + ".CHECKSUM", timeout=self.timeout)
                    if c.status_code == 200:
                        checksum_file.write_text(c.text)
                        if not self._verify(target, c.text):
                            target.unlink(missing_ok=True)
                            raise ValueError("checksum mismatch")
                    self.stats["downloaded"] += 1
                    return target
                except (httpx.HTTPError, ValueError) as exc:
                    if attempt == self.retries - 1:
                        log.error("download failed %s: %s", f.url, exc)
                        self.stats["failed"] += 1
                        return None
                    await asyncio.sleep(min(2**attempt * 0.5, 8.0))
        return None

    @staticmethod
    def _verify(path: Path, checksum_text: str) -> bool:
        expected = checksum_text.strip().split()[0] if checksum_text.strip() else ""
        if not expected:
            return True
        h = hashlib.sha256(path.read_bytes()).hexdigest()
        return h.lower() == expected.lower()

    async def fetch_many(self, files: list[RemoteFile], *, force: bool = False) -> list[Path]:
        headers = {"User-Agent": "tradebot/0.1 (research)"}
        limits = httpx.Limits(max_connections=self.concurrency, max_keepalive_connections=self.concurrency)
        async with httpx.AsyncClient(headers=headers, limits=limits, follow_redirects=True) as client:
            results = await asyncio.gather(*(self.fetch_one(client, f, force=force) for f in files))
        return [p for p in results if p is not None]

    def sync(
        self,
        symbols: list[str],
        start: date,
        end: date,
        *,
        interval: str = "1m",
        klines: bool = True,
        funding: bool = True,
        mark_price: bool = False,
    ) -> dict:
        self.paths.ensure()
        files: list[RemoteFile] = []
        for s in symbols:
            if klines:
                files += iter_files("klines", s, interval, start, end)
            if funding:
                files += iter_files("fundingRate", s, "", start, end)
            if mark_price:
                files += iter_files("markPriceKlines", s, interval, start, end)
        log.info("syncing %d files for %d symbols", len(files), len(symbols))
        asyncio.run(self.fetch_many(files))
        return dict(self.stats)
