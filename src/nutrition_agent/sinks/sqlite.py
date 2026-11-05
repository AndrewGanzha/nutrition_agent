"""SQLite sink: local audit trail, dedupe ledger and replay source.

``sink_writes`` is shared state: every sink records what it already wrote for
an item, which is how a retried call (``commit`` after ``partial``) or a later
backfill avoids duplicates. Reads go through :meth:`entries_on`; ``raw_json``
is written for audit/replay and is deliberately not exposed as data.
"""

from __future__ import annotations

import asyncio
import os
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any

import aiosqlite

from nutrition_agent.domain import NutritionEntry
from nutrition_agent.sinks.base import ItemWrite, SinkResult

SCHEMA = """
CREATE TABLE IF NOT EXISTS entries (
    id TEXT PRIMARY KEY,
    user_id TEXT,
    eaten_at TEXT NOT NULL,
    meal_type TEXT NOT NULL,
    source TEXT NOT NULL,
    raw_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS items (
    entry_id TEXT NOT NULL,
    idx INTEGER NOT NULL,
    name TEXT NOT NULL,
    grams REAL,
    kcal REAL,
    protein_g REAL,
    fat_g REAL,
    carbs_g REAL,
    confidence REAL,
    PRIMARY KEY (entry_id, idx)
);
CREATE TABLE IF NOT EXISTS sink_writes (
    entry_id TEXT NOT NULL,
    sink TEXT NOT NULL,
    item_idx INTEGER NOT NULL,
    external_id TEXT,
    status TEXT NOT NULL,
    written_at TEXT NOT NULL,
    PRIMARY KEY (entry_id, sink, item_idx)
);
"""


async def ensure_schema(db: aiosqlite.Connection) -> None:
    """Create the shared tables; ``IF NOT EXISTS`` makes this idempotent."""
    await db.executescript(SCHEMA)


async def finished_item_indexes(
    db: aiosqlite.Connection, entry_id: str, sink: str
) -> set[int]:
    """Indexes this sink already stored (``written``/``skipped``) for the entry."""
    cursor = await db.execute(
        "SELECT item_idx FROM sink_writes"
        " WHERE entry_id = ? AND sink = ? AND status IN ('written', 'skipped')",
        (entry_id, sink),
    )
    rows = await cursor.fetchall()
    await cursor.close()
    return {int(row[0]) for row in rows}


async def record_item_write(
    db: aiosqlite.Connection,
    entry_id: str,
    sink: str,
    index: int,
    *,
    status: str,
    external_id: str | None = None,
) -> None:
    """Upsert one ledger row; the callers own the transaction."""
    await db.execute(
        """
        INSERT INTO sink_writes (entry_id, sink, item_idx, external_id, status, written_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(entry_id, sink, item_idx) DO UPDATE SET
            external_id = excluded.external_id,
            status = excluded.status,
            written_at = excluded.written_at
        """,
        (entry_id, sink, index, external_id, status, _now()),
    )


class LocalSQLiteSink:
    """Stores entries, items and per-sink write records in one SQLite file."""

    name = "sqlite"

    def __init__(self, path: str | Path = "nutrition.db", *, timeout: float = 5.0) -> None:
        self._path = str(path)
        self._timeout = timeout

    async def write(self, entry: NutritionEntry, *, user_id: str | None = None) -> SinkResult:
        entry_id = str(entry.id)
        items: list[ItemWrite] = []
        async with aiosqlite.connect(self._path, timeout=self._timeout) as db:
            await ensure_schema(db)
            done = await finished_item_indexes(db, entry_id, self.name)
            await db.execute(
                """
                INSERT OR IGNORE INTO entries
                    (id, user_id, eaten_at, meal_type, source, raw_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    entry_id,
                    user_id,
                    entry.eaten_at.astimezone(UTC).isoformat(),
                    entry.meal_type.value,
                    entry.source,
                    entry.model_dump_json(),
                    _now(),
                ),
            )
            for index, item in enumerate(entry.items):
                await db.execute(
                    """
                    INSERT OR IGNORE INTO items
                        (entry_id, idx, name, grams, kcal, protein_g, fat_g, carbs_g, confidence)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        entry_id,
                        index,
                        item.name,
                        item.grams,
                        item.kcal,
                        item.protein_g,
                        item.fat_g,
                        item.carbs_g,
                        item.confidence,
                    ),
                )
                if index in done:
                    items.append(ItemWrite(index=index, status="skipped"))
                    continue
                external_id = f"{entry_id}:{index}"
                await record_item_write(
                    db,
                    entry_id,
                    self.name,
                    index,
                    status="written",
                    external_id=external_id,
                )
                items.append(ItemWrite(index=index, status="written", external_id=external_id))
            await db.commit()
        return SinkResult(name=self.name, items=items)

    async def entries_on(self, day: date, *, user_id: str | None = None) -> list[NutritionEntry]:
        """Entries whose ``eaten_at`` falls on ``day`` (UTC), oldest first.

        ``user_id=None`` selects anonymous writes (``user_id IS NULL``), matching
        how :meth:`write` stores a missing user.
        """
        if not await asyncio.to_thread(os.path.exists, self._path):
            return []
        start = datetime.combine(day, time.min, tzinfo=UTC)
        end = start + timedelta(days=1)
        query = "SELECT raw_json FROM entries WHERE eaten_at >= ? AND eaten_at < ?"
        params: list[Any] = [start.isoformat(), end.isoformat()]
        if user_id is None:
            query += " AND user_id IS NULL"
        else:
            query += " AND user_id = ?"
            params.append(user_id)
        query += " ORDER BY eaten_at, id"

        async with aiosqlite.connect(self._path, timeout=self._timeout) as db:
            cursor = await db.execute(query, params)
            rows = await cursor.fetchall()
            await cursor.close()

        return [NutritionEntry.model_validate_json(row[0]) for row in rows]


def _now() -> str:
    return datetime.now(UTC).isoformat()


__all__ = [
    "SCHEMA",
    "LocalSQLiteSink",
    "ensure_schema",
    "finished_item_indexes",
    "record_item_write",
]
