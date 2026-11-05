"""Tests for :mod:`nutrition_agent.results`."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from nutrition_agent.domain import FoodItem, NutritionEntry
from nutrition_agent.results import ProcessResult
from nutrition_agent.sinks import ItemWrite, SinkResult


def _entry(item_count: int) -> NutritionEntry:
    return NutritionEntry(
        eaten_at=datetime(2026, 11, 5, 12, 30, tzinfo=UTC),
        source="text",
        items=[FoodItem(name=f"item-{index}") for index in range(item_count)],
    )


def test_sink_lists_are_derived_from_sink_results() -> None:
    result = ProcessResult(
        status="partial",
        sink_results=[
            SinkResult(
                name="sqlite",
                items=[ItemWrite(index=0, status="written"), ItemWrite(index=1, status="skipped")],
            ),
            SinkResult(
                name="google_health",
                items=[
                    ItemWrite(index=0, status="written", external_id="gp-1"),
                    ItemWrite(index=1, status="failed", error="401"),
                ],
            ),
        ],
    )

    assert result.sinks_succeeded == ["sqlite"]
    assert result.sinks_failed == ["google_health"]


def test_no_sink_results_means_no_sink_names() -> None:
    result = ProcessResult(status="ok")

    assert result.sinks_succeeded == []
    assert result.sinks_failed == []


def test_sink_with_empty_result_counts_as_failed() -> None:
    result = ProcessResult(
        status="partial",
        entry=_entry(2),
        sink_results=[SinkResult(name="sqlite", items=[])],
    )

    assert result.sinks_succeeded == []
    assert result.sinks_failed == ["sqlite"]


def test_sink_missing_an_item_counts_as_failed() -> None:
    result = ProcessResult(
        status="partial",
        entry=_entry(2),
        sink_results=[
            SinkResult(name="sqlite", items=[ItemWrite(index=0, status="written")]),
        ],
    )

    assert result.sinks_failed == ["sqlite"]


def test_extra_indexes_outside_the_entry_count_as_failed() -> None:
    result = ProcessResult(
        status="partial",
        entry=_entry(1),
        sink_results=[
            SinkResult(
                name="sqlite",
                items=[ItemWrite(index=0, status="written"), ItemWrite(index=1, status="written")],
            ),
        ],
    )

    assert result.sinks_failed == ["sqlite"]


def test_all_skipped_items_still_count_as_coverage() -> None:
    result = ProcessResult(
        status="ok",
        entry=_entry(2),
        sink_results=[
            SinkResult(
                name="sqlite",
                items=[ItemWrite(index=0, status="skipped"), ItemWrite(index=1, status="skipped")],
            ),
        ],
    )

    assert result.sinks_succeeded == ["sqlite"]


def test_unknown_status_rejected() -> None:
    with pytest.raises(ValueError, match="unknown process status"):
        ProcessResult(status="weird")  # type: ignore[arg-type]
