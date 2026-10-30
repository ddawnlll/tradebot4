"""Canonical data paths and dataset hashing."""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(os.environ.get("TRADEBOT_HOME", Path(__file__).resolve().parents[3])).resolve()


@dataclass(frozen=True, slots=True)
class Paths:
    root: Path = ROOT

    @property
    def data(self) -> Path:
        return self.root / "data"

    @property
    def raw(self) -> Path:
        return self.data / "raw"

    @property
    def market(self) -> Path:
        return self.data / "market"

    @property
    def bars_1m(self) -> Path:
        return self.market / "bars_1m"

    @property
    def bars_1d(self) -> Path:
        return self.market / "bars_1d"

    @property
    def funding(self) -> Path:
        return self.market / "funding"

    @property
    def mark_price(self) -> Path:
        return self.market / "mark_price"

    @property
    def instruments(self) -> Path:
        return self.market / "instruments"

    @property
    def universe(self) -> Path:
        return self.market / "universe"

    @property
    def catalog(self) -> Path:
        return self.data / "nautilus_catalog"

    @property
    def journal(self) -> Path:
        return self.data / "journal"

    @property
    def runs(self) -> Path:
        return self.root / "runs"

    @property
    def reports(self) -> Path:
        return self.root / "reports"

    def ensure(self) -> None:
        for p in (
            self.raw,
            self.bars_1m,
            self.bars_1d,
            self.funding,
            self.mark_price,
            self.instruments,
            self.universe,
            self.catalog,
            self.journal,
            self.runs,
            self.reports,
        ):
            p.mkdir(parents=True, exist_ok=True)


def file_sha256(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def dataset_hash(files: list[Path], *, limit: int | None = None) -> str:
    """Hash a set of parquet files by (relative path, size, sha256)."""
    h = hashlib.sha256()
    for p in sorted(files):
        if p.suffix != ".parquet" or not p.exists():
            continue
        h.update(str(p.name).encode())
        h.update(str(p.stat().st_size).encode())
        h.update(file_sha256(p).encode())
        if limit is not None and h.digest() == b"":
            break
    return h.hexdigest()[:32]


def write_metadata(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str))
