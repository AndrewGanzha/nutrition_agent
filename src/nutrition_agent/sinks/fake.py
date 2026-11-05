"""In-memory sink for tests."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from nutrition_agent.domain import NutritionEntry
from nutrition_agent.sinks.base import ItemStatus, ItemWrite, SinkResult


@dataclass(frozen=True)
class SinkCall:
    """One recorded ``FakeSink.write`` invocation."""

    entry: NutritionEntry
    user_id: str | None


class FakeSink:
    """Test sink: records calls and returns configurable per-item statuses.

    ``statuses`` maps an item index to its status; indexes not mentioned fall
    back to ``default_status``. ``raises`` makes ``write`` raise after recording
    the call, which is how a crashing sink is simulated.
    """

    def __init__(
        self,
        name: str = "fake",
        *,
        statuses: Mapping[int, ItemStatus] | None = None,
        default_status: ItemStatus = "written",
        error: str = "fake failure",
        raises: Exception | None = None,
    ) -> None:
        self.name = name
        self.calls: list[SinkCall] = []
        self._statuses = dict(statuses or {})
        self._default_status = default_status
        self._error = error
        self._raises = raises

    async def write(self, entry: NutritionEntry, *, user_id: str | None = None) -> SinkResult:
        self.calls.append(SinkCall(entry=entry, user_id=user_id))
        if self._raises is not None:
            raise self._raises
        items = [self._item_write(index) for index in range(len(entry.items))]
        return SinkResult(name=self.name, items=items)

    def _item_write(self, index: int) -> ItemWrite:
        status = self._statuses.get(index, self._default_status)
        return ItemWrite(
            index=index,
            status=status,
            external_id=f"{self.name}:{index}" if status == "written" else None,
            error=self._error if status == "failed" else None,
        )


__all__ = ["FakeSink", "SinkCall"]
