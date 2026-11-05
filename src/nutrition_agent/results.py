"""Public result of a single agent call."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from nutrition_agent.domain import NutritionEntry
from nutrition_agent.sinks.base import SinkResult

ProcessStatus = Literal["ok", "partial", "ignored", "parse_failed", "invalid_input", "error"]

_PROCESS_STATUSES: frozenset[str] = frozenset(
    ("ok", "partial", "ignored", "parse_failed", "invalid_input", "error")
)


@dataclass(frozen=True)
class ProcessResult:
    """What :meth:`NutritionAgent.process` returns to the caller.

    ``sinks_succeeded`` / ``sinks_failed`` are derived from ``sink_results``:
    a sink counts as failed when at least one of its items is ``failed``.
    """

    status: ProcessStatus
    entry: NutritionEntry | None = None
    transcript: str | None = None
    sink_results: list[SinkResult] = field(default_factory=list)
    commit_token: str | None = None
    raw_response: str | None = None
    error: str | None = None

    def __post_init__(self) -> None:
        if self.status not in _PROCESS_STATUSES:
            raise ValueError(f"unknown process status: {self.status!r}")

    @property
    def sinks_succeeded(self) -> list[str]:
        return [
            result.name
            for result in self.sink_results
            if all(item.status != "failed" for item in result.items)
        ]

    @property
    def sinks_failed(self) -> list[str]:
        return [
            result.name
            for result in self.sink_results
            if any(item.status == "failed" for item in result.items)
        ]


__all__ = ["ProcessResult", "ProcessStatus"]
