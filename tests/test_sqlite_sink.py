"""Tests for :mod:`nutrition_agent.sinks.sqlite` (file-backed, no network)."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, date, datetime, timedelta, timezone
from pathlib import Path

from nutrition_agent.domain import FoodItem, NutritionEntry
from nutrition_agent.sinks import LocalSQLiteSink, Sink

DAY = date(2026, 11, 5)


def _entry(
    *,
    eaten_at: datetime = datetime(2026, 11, 5, 12, 30, tzinfo=UTC),
    item_count: int = 2,
) -> NutritionEntry:
    return NutritionEntry(
        eaten_at=eaten_at,
        source="text",
        items=[
            FoodItem(name=f"item-{index}", grams=100 + index, kcal=200)
            for index in range(item_count)
        ],
    )


def _table_counts(path: Path) -> tuple[int, int, int]:
    with sqlite3.connect(path) as db:
        return tuple(
            db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in ("entries", "items", "sink_writes")
        )


async def test_second_write_skips_items_and_keeps_one_row_each(tmp_path: Path) -> None:
    path = tmp_path / "nutrition.db"
    sink = LocalSQLiteSink(path)
    entry = _entry()

    first = await sink.write(entry)
    second = await sink.write(entry)

    assert [item.status for item in first.items] == ["written", "written"]
    assert [item.external_id for item in first.items] == [
        f"{entry.id}:0",
        f"{entry.id}:1",
    ]
    assert [item.status for item in second.items] == ["skipped", "skipped"]
    assert [item.external_id for item in second.items] == [None, None]
    assert _table_counts(path) == (1, 2, 2)


async def test_raw_json_is_stored_and_replayable(tmp_path: Path) -> None:
    path = tmp_path / "nutrition.db"
    sink = LocalSQLiteSink(path)
    entry = _entry()

    await sink.write(entry, user_id="u-1")

    with sqlite3.connect(path) as db:
        raw, user_id = db.execute("SELECT raw_json, user_id FROM entries").fetchone()
    assert user_id == "u-1"
    assert NutritionEntry.model_validate_json(raw) == entry
    assert json.loads(raw)["items"][0]["name"] == "item-0"


async def test_write_fills_only_items_missing_from_ledger(tmp_path: Path) -> None:
    path = tmp_path / "nutrition.db"
    sink = LocalSQLiteSink(path)
    entry = _entry()

    await sink.write(entry)
    with sqlite3.connect(path) as db:
        db.execute("DELETE FROM sink_writes WHERE item_idx = 1")

    second = await sink.write(entry)

    assert [item.status for item in second.items] == ["skipped", "written"]
    assert second.items[1].external_id == f"{entry.id}:1"
    assert _table_counts(path) == (1, 2, 2)


async def test_entries_on_filters_by_utc_day_and_user(tmp_path: Path) -> None:
    sink = LocalSQLiteSink(tmp_path / "nutrition.db")
    lunch = _entry(item_count=1)
    dinner = _entry(eaten_at=datetime(2026, 11, 5, 19, 0, tzinfo=UTC), item_count=1)
    tomorrow = _entry(eaten_at=datetime(2026, 11, 6, 8, 0, tzinfo=UTC), item_count=1)
    anonymous = _entry(eaten_at=datetime(2026, 11, 5, 9, 0, tzinfo=UTC), item_count=1)

    await sink.write(lunch, user_id="alice")
    await sink.write(dinner, user_id="bob")
    await sink.write(tomorrow, user_id="alice")
    await sink.write(anonymous)

    assert await sink.entries_on(DAY, user_id="alice") == [lunch]
    assert await sink.entries_on(DAY, user_id="bob") == [dinner]
    assert await sink.entries_on(DAY) == [anonymous]
    assert await sink.entries_on(date(2026, 11, 6), user_id="alice") == [tomorrow]


async def test_entries_on_missing_database_is_empty_and_creates_nothing(tmp_path: Path) -> None:
    path = tmp_path / "absent.db"

    assert await LocalSQLiteSink(path).entries_on(DAY) == []
    assert not path.exists()


def test_sqlite_sink_satisfies_protocol(tmp_path: Path) -> None:
    sink: Sink = LocalSQLiteSink(tmp_path / "nutrition.db")

    assert isinstance(sink, Sink)
    assert sink.name == "sqlite"


async def test_entries_on_uses_utc_bounds_and_orders_by_eaten_at(tmp_path: Path) -> None:
    sink = LocalSQLiteSink(tmp_path / "nutrition.db")
    midnight = _entry(eaten_at=datetime(2026, 11, 5, 0, 0, tzinfo=UTC), item_count=1)
    last_microsecond = _entry(
        eaten_at=datetime(2026, 11, 5, 23, 59, 59, 999999, tzinfo=UTC), item_count=1
    )
    next_midnight = _entry(eaten_at=datetime(2026, 11, 6, 0, 0, tzinfo=UTC), item_count=1)
    # 01:00+03:00 is 22:00Z on the previous day: the day filter is UTC-based.
    offset_entry = _entry(
        eaten_at=datetime(2026, 11, 6, 1, 0, tzinfo=timezone(timedelta(hours=3))), item_count=1
    )
    dinner = _entry(eaten_at=datetime(2026, 11, 5, 19, 0, tzinfo=UTC), item_count=1)

    for entry in (dinner, next_midnight, last_microsecond, offset_entry, midnight):
        await sink.write(entry, user_id="a")

    assert await sink.entries_on(DAY, user_id="a") == [
        midnight,
        dinner,
        offset_entry,
        last_microsecond,
    ]


async def test_failed_ledger_row_is_retried_as_written(tmp_path: Path) -> None:
    path = tmp_path / "nutrition.db"
    sink = LocalSQLiteSink(path)
    entry = _entry()

    await sink.write(entry)
    with sqlite3.connect(path) as db:
        db.execute("UPDATE sink_writes SET status = 'failed' WHERE item_idx = 1")

    second = await sink.write(entry)

    assert [item.status for item in second.items] == ["skipped", "written"]
    with sqlite3.connect(path) as db:
        statuses = dict(db.execute("SELECT item_idx, status FROM sink_writes"))
    assert statuses == {0: "written", 1: "written"}


async def test_items_row_is_restored_even_when_ledger_says_written(tmp_path: Path) -> None:
    path = tmp_path / "nutrition.db"
    sink = LocalSQLiteSink(path)
    entry = _entry()

    await sink.write(entry)
    with sqlite3.connect(path) as db:
        db.execute("DELETE FROM items WHERE idx = 1")

    second = await sink.write(entry)

    assert [item.status for item in second.items] == ["skipped", "skipped"]
    assert _table_counts(path) == (1, 2, 2)


async def test_entries_on_existing_uninitialised_database_is_empty(tmp_path: Path) -> None:
    path = tmp_path / "empty.db"
    path.write_bytes(b"")

    assert await LocalSQLiteSink(path).entries_on(DAY) == []
