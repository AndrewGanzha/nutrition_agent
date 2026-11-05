"""Tests for :mod:`nutrition_agent.results`."""

from __future__ import annotations

import pytest

from nutrition_agent.results import ProcessResult
from nutrition_agent.sinks import ItemWrite, SinkResult


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


def test_unknown_status_rejected() -> None:
    with pytest.raises(ValueError, match="unknown process status"):
        ProcessResult(status="weird")  # type: ignore[arg-type]
