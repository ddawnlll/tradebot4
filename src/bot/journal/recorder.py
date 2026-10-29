"""SQLite WAL journal writer.

Writes are batched in a single transaction. The journal is append-only: there
is no update or delete API. Tags are separate rows, so correcting a tag adds a
new row and leaves the original history intact.
"""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any

from bot.journal.schema import SCHEMA_VERSION, ddl


class Journal:
    def __init__(self, path: Path, source: str = "backtest", run_id: str | None = None) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.source = source
        self.run_id = run_id or uuid.uuid4().hex[:16]
        self._conn = sqlite3.connect(self.path)
        self._conn.executescript(ddl())
        self._pending: list[tuple] = []
        self._pending_tags: list[tuple] = []

    def close(self) -> None:
        self.flush()
        self._conn.close()

    def __enter__(self) -> Journal:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # ------------------------------------------------------------------ write
    def record(
        self,
        event_type: str,
        *,
        ts_event_ms: int,
        payload: dict[str, Any],
        symbol: str | None = None,
        signal_id: str | None = None,
        strategy_version: str | None = None,
        config_hash: str | None = None,
    ) -> str:
        event_id = uuid.uuid4().hex
        self._pending.append(
            (
                event_id,
                SCHEMA_VERSION,
                self.source,
                event_type,
                int(ts_event_ms),
                int(time.time() * 1000),
                symbol,
                signal_id,
                strategy_version,
                config_hash,
                self.run_id,
                json.dumps(payload, separators=(",", ":"), default=str),
            )
        )
        if len(self._pending) >= 5000:
            self.flush()
        return event_id

    def add_tag(self, trade_id: str, tag: str, *, source: str = "rule", rule_version: str = "", notes: str = "") -> None:
        self._pending_tags.append(
            (uuid.uuid4().hex, trade_id, tag, source, rule_version, notes, int(time.time() * 1000))
        )
        if len(self._pending_tags) >= 1000:
            self.flush()

    def record_run(self, metadata: dict[str, Any], *, strategy: str, strategy_version: str, config_hash: str, designation: str = "research") -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO runs(run_id, created_ms, source, strategy, strategy_version, config_hash, nautilus_version,"
            " python_version, data_hash, seed, designation, metadata) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                self.run_id,
                int(time.time() * 1000),
                self.source,
                strategy,
                strategy_version,
                config_hash,
                metadata.get("nautilus_version"),
                metadata.get("python"),
                metadata.get("data_hash"),
                metadata.get("seed"),
                designation,
                json.dumps(metadata, default=str),
            ),
        )
        self._conn.commit()

    def flush(self) -> None:
        if self._pending:
            self._conn.executemany(
                "INSERT OR IGNORE INTO events(event_id, schema_version, source, event_type, ts_event_ms, ts_recorded_ms,"
                " symbol, signal_id, strategy_version, config_hash, run_id, payload) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                self._pending,
            )
            self._pending.clear()
        if self._pending_tags:
            self._conn.executemany(
                "INSERT OR IGNORE INTO tags(tag_id, trade_id, tag, source, rule_version, notes, ts_ms) VALUES (?,?,?,?,?,?,?)",
                self._pending_tags,
            )
            self._pending_tags.clear()
        self._conn.commit()

    # ------------------------------------------------------------------- read
    def count(self, event_type: str | None = None) -> int:
        self.flush()
        if event_type:
            row = self._conn.execute("SELECT COUNT(*) FROM events WHERE event_type=?", (event_type,)).fetchone()
        else:
            row = self._conn.execute("SELECT COUNT(*) FROM events").fetchone()
        return int(row[0]) if row else 0


class NullJournal:
    """No-op recorder for tests and dry runs."""

    run_id = "null"

    def record(self, *args, **kwargs) -> str:
        return ""

    def add_tag(self, *args, **kwargs) -> None:
        return None

    def record_run(self, *args, **kwargs) -> None:
        return None

    def flush(self) -> None:
        return None

    def close(self) -> None:
        return None

    def __enter__(self):
        return self

    def __exit__(self, *exc) -> None:
        pass
