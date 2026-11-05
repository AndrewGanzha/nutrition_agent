"""Public result of a single agent call."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, get_args

from nutrition_agent.domain import NutritionEntry
from nutrition_agent.sinks.base import SinkResult

ProcessStatus = Literal["ok", "partial", "ignored", "parse_failed", "invalid_input", "error"]

_PROCESS_STATUSES = get_args(ProcessStatus)


@dataclass(frozen=True)
class ProcessResult:
    """What :meth:`NutritionAgent.process` returns to the caller.

    ``sinks_succeeded`` / ``sinks_failed`` are derived from ``sink_results``:
    a sink counts as failed when any item is ``failed`` or when its results do
    not cover every item of ``entry`` (indexes ``0..n-1`` exactly once).
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
        return [result.name for result in self.sink_results if not self._sink_failed(result)]

    @property
    def sinks_failed(self) -> list[str]:
        return [result.name for result in self.sink_results if self._sink_failed(result)]

    def _sink_failed(self, result: SinkResult) -> bool:
        """A sink failed if it reported a failure, or did not cover the entry.

        Covering means one :class:`ItemWrite` per entry item with indexes
        ``0..n-1``. Without that check an empty or sparse ``SinkResult`` would
        vacuously count as success and turn a broken sink into a silent ``ok``.
        """
        if any(item.status == "failed" for item in result.items):
            return True
        if self.entry is None:
            return False
        expected = set(range(len(self.entry.items)))
        return {item.index for item in result.items} != expected


__all__ = ["ProcessResult", "ProcessStatus"]
