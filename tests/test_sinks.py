"""Tests for :mod:`nutrition_agent.sinks`."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from nutrition_agent.domain import FoodItem, NutritionEntry
from nutrition_agent.sinks import (
    FakeSink,
    ItemStatus,
    ItemWrite,
    Sink,
    SinkCall,
    SinkResult,
)


def _entry(item_count: int = 1) -> NutritionEntry:
    return NutritionEntry(
        eaten_at=datetime(2026, 11, 5, 12, 30, tzinfo=UTC),
        source="text",
        items=[FoodItem(name=f"item-{index}") for index in range(item_count)],
    )


def test_item_write_rejects_unknown_status() -> None:
    with pytest.raises(ValueError, match="unknown item status"):
        ItemWrite(index=0, status="done")  # type: ignore[arg-type]


def test_item_write_rejects_negative_index() -> None:
    with pytest.raises(ValueError, match="non-negative"):
        ItemWrite(index=-1, status="written")


@pytest.mark.parametrize("name", ["", "   "])
def test_sink_result_rejects_blank_name(name: str) -> None:
    with pytest.raises(ValueError, match="name must not be empty"):
        SinkResult(name=name)


def test_sink_result_rejects_duplicate_item_indexes() -> None:
    items = [ItemWrite(index=0, status="written"), ItemWrite(index=0, status="skipped")]

    with pytest.raises(ValueError, match="duplicate item indexes"):
        SinkResult(name="sqlite", items=items)


async def test_fake_sink_reports_status_per_item_and_records_calls() -> None:
    statuses: dict[int, ItemStatus] = {1: "skipped", 2: "failed"}
    sink = FakeSink(statuses=statuses)
    entry = _entry(3)

    result = await sink.write(entry, user_id="user-1")

    assert result.name == "fake"
    assert [item.status for item in result.items] == ["written", "skipped", "failed"]
    assert [item.external_id for item in result.items] == ["fake:0", None, None]
    assert [item.error for item in result.items] == [None, None, "fake failure"]
    assert sink.calls == [SinkCall(entry=entry, user_id="user-1")]


async def test_fake_sink_records_call_before_raising() -> None:
    sink = FakeSink(raises=RuntimeError("boom"))

    with pytest.raises(RuntimeError, match="boom"):
        await sink.write(_entry(), user_id="user-1")

    assert [call.user_id for call in sink.calls] == ["user-1"]


def test_fake_sink_satisfies_sink_protocol() -> None:
    sink: Sink = FakeSink(name="sqlite")

    assert isinstance(sink, Sink)
    assert sink.name == "sqlite"
