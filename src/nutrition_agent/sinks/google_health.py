"""Google Health v4 ``nutrition-log`` sink.

One data point per food item, anonymous food (no ``food`` reference), written
at-least-once: the ledger in ``sink_writes`` records what already landed, and a
retry only posts items that are still missing. HTTP errors are per item — one
failed item never aborts the rest of the entry.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Protocol

import aiosqlite
import httpx

from nutrition_agent.domain import FoodItem, MealType, NutritionEntry
from nutrition_agent.sinks.base import ItemWrite, SinkResult
from nutrition_agent.sinks.sqlite import (
    ensure_schema,
    finished_item_indexes,
    record_item_write,
)

URL = "https://health.googleapis.com/v4/users/me/dataTypes/nutrition-log/dataPoints"

_MEAL_TYPES = {
    MealType.BREAKFAST: "BREAKFAST",
    MealType.LUNCH: "LUNCH",
    MealType.DINNER: "DINNER",
    MealType.SNACK: "SNACK",
}


class TokenProvider(Protocol):
    """Returns an OAuth access token for the given user (see docs/google_health.md)."""

    async def __call__(self, user_id: str | None) -> str: ...


class GoogleHealthSink:
    """Writes every food item as a separate Google Health nutrition data point."""

    name = "google_health"

    def __init__(
        self,
        sqlite_path: str | Path,
        *,
        token_provider: TokenProvider,
        meal_duration: timedelta = timedelta(minutes=30),
        timeout: float = 10.0,
        sqlite_timeout: float = 5.0,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._path = str(sqlite_path)
        self._token_provider = token_provider
        self._meal_duration = meal_duration
        self._timeout = timeout
        self._sqlite_timeout = sqlite_timeout
        self._http = http_client

    async def write(self, entry: NutritionEntry, *, user_id: str | None = None) -> SinkResult:
        entry_id = str(entry.id)
        async with aiosqlite.connect(self._path, timeout=self._sqlite_timeout) as db:
            await ensure_schema(db)
            done = await finished_item_indexes(db, entry_id, self.name)
            pending = [index for index in range(len(entry.items)) if index not in done]
            if not pending:
                return _result(
                    [ItemWrite(index=index, status="skipped") for index in range(len(entry.items))]
                )

            try:
                token = await self._token_provider(user_id)
            except Exception as exc:  # a broken auth callback fails items, not the chain
                return _result(
                    [
                        _item_write(index, done, f"token provider failed: {exc}")
                        for index in range(len(entry.items))
                    ]
                )

            if self._http is not None:
                items = await self._post_items(db, entry_id, entry, done, token, self._http)
            else:
                async with httpx.AsyncClient(timeout=self._timeout) as client:
                    items = await self._post_items(db, entry_id, entry, done, token, client)
            await db.commit()
        return _result(items)

    async def _post_items(
        self,
        db: aiosqlite.Connection,
        entry_id: str,
        entry: NutritionEntry,
        done: set[int],
        token: str,
        client: httpx.AsyncClient,
    ) -> list[ItemWrite]:
        headers = {"Authorization": f"Bearer {token}"}
        items: list[ItemWrite] = []
        for index, item in enumerate(entry.items):
            if index in done:
                items.append(ItemWrite(index=index, status="skipped"))
                continue
            payload = _payload(entry, item, self._meal_duration)
            try:
                response = await client.post(URL, json=payload, headers=headers)
            except httpx.HTTPError as exc:
                await record_item_write(db, entry_id, self.name, index, status="failed")
                await db.commit()
                items.append(
                    ItemWrite(index=index, status="failed", error=f"request failed: {exc}")
                )
                continue
            if not response.is_success:
                await record_item_write(db, entry_id, self.name, index, status="failed")
                await db.commit()
                items.append(ItemWrite(index=index, status="failed", error=_http_error(response)))
                continue
            body = _json_body(response)
            operation_error = _operation_error(body)
            if operation_error is not None:
                await record_item_write(db, entry_id, self.name, index, status="failed")
                await db.commit()
                items.append(ItemWrite(index=index, status="failed", error=operation_error))
                continue
            external_id = _operation_name(body)
            await record_item_write(
                db, entry_id, self.name, index, status="written", external_id=external_id
            )
            # Commit per landed item: if the loop aborts later, successes stay
            # recorded and a retry cannot duplicate the data points.
            await db.commit()
            items.append(ItemWrite(index=index, status="written", external_id=external_id))
        return items


def _result(items: list[ItemWrite]) -> SinkResult:
    return SinkResult(name=GoogleHealthSink.name, items=items)


def _item_write(index: int, done: set[int], error: str) -> ItemWrite:
    if index in done:
        return ItemWrite(index=index, status="skipped")
    return ItemWrite(index=index, status="failed", error=error)


def _payload(entry: NutritionEntry, item: FoodItem, duration: timedelta) -> dict[str, Any]:
    start = entry.eaten_at
    end = start + duration
    log: dict[str, Any] = {
        "interval": {
            "startTime": start.isoformat(),
            "startUtcOffset": _utc_offset(start),
            "endTime": end.isoformat(),
            "endUtcOffset": _utc_offset(end),
        },
        "foodDisplayName": (
            f"{item.name}, {item.grams:g} г" if item.grams is not None else item.name
        ),
    }
    if entry.meal_type is not MealType.UNKNOWN:
        log["mealType"] = _MEAL_TYPES[entry.meal_type]
    if item.kcal is not None:
        log["energy"] = {"kcal": item.kcal}
    if item.carbs_g is not None:
        log["totalCarbohydrate"] = {"grams": item.carbs_g}
    if item.fat_g is not None:
        log["totalFat"] = {"grams": item.fat_g}
    if item.protein_g is not None:
        log["nutrients"] = [{"nutrient": "PROTEIN", "quantity": {"grams": item.protein_g}}]
    return {"nutritionLog": log}


def _utc_offset(value: datetime) -> str:
    offset = value.utcoffset() or timedelta(0)
    return f"{int(offset.total_seconds())}s"


def _json_body(response: httpx.Response) -> dict[str, Any] | None:
    try:
        body = response.json()
    except json.JSONDecodeError:
        return None
    return body if isinstance(body, dict) else None


def _operation_error(body: dict[str, Any] | None) -> str | None:
    """A completed-but-failed Operation still comes back with HTTP 2xx."""
    if body is None or body.get("error") is None:
        return None
    error = body["error"]
    message = error.get("message") if isinstance(error, dict) else None
    return (
        f"operation failed: {message}"
        if isinstance(message, str) and message
        else ("operation failed")
    )


def _operation_name(body: dict[str, Any] | None) -> str | None:
    """Best-effort data point name from the long-running operation body."""
    if body is None:
        return None
    result = body.get("response")
    if not isinstance(result, dict):
        return None
    name = result.get("name")
    return name if isinstance(name, str) and name else None


def _http_error(response: httpx.Response) -> str:
    detail = " ".join(response.text.split())[:200]
    return f"HTTP {response.status_code}: {detail}" if detail else f"HTTP {response.status_code}"


__all__ = ["URL", "GoogleHealthSink", "TokenProvider"]
