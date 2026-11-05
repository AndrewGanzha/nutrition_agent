"""Sink contract and its result types.

A sink persists a parsed :class:`~nutrition_agent.domain.NutritionEntry` and
reports one status per item. Statuses are the only thing the agent uses to
decide between ``ok``, ``partial`` and ``error``, so they are validated at
construction time instead of being trusted from arbitrary implementations.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Protocol, get_args, runtime_checkable

from nutrition_agent.domain import NutritionEntry

ItemStatus = Literal["written", "skipped", "failed"]

_ITEM_STATUSES = get_args(ItemStatus)


@dataclass(frozen=True)
class ItemWrite:
    """Outcome of writing one entry item to one sink."""

    index: int
    status: ItemStatus
    external_id: str | None = None
    error: str | None = None

    def __post_init__(self) -> None:
        if self.status not in _ITEM_STATUSES:
            raise ValueError(f"unknown item status: {self.status!r}")
        if self.index < 0:
            raise ValueError("item index must be non-negative")


@dataclass(frozen=True)
class SinkResult:
    """What one sink did with every item of an entry."""

    name: str
    items: list[ItemWrite] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("sink name must not be empty")
        indexes = [item.index for item in self.items]
        if len(set(indexes)) != len(indexes):
            raise ValueError(f"sink {self.name!r} returned duplicate item indexes")


@runtime_checkable
class Sink(Protocol):
    """Destination for a parsed entry (SQLite, Google Health, third-party)."""

    name: str

    async def write(self, entry: NutritionEntry, *, user_id: str | None = None) -> SinkResult: ...


__all__ = ["ItemStatus", "ItemWrite", "Sink", "SinkResult"]
