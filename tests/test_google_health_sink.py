"""Tests for :mod:`nutrition_agent.sinks.google_health` (HTTP mocked, no network)."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx

from nutrition_agent.domain import FoodItem, MealType, NutritionEntry
from nutrition_agent.sinks import GoogleHealthSink, Sink
from nutrition_agent.sinks.google_health import URL

EATEN_AT = datetime(2026, 11, 5, 12, 30, tzinfo=UTC)


class Recorder:
    """MockTransport handler that records requests and replays responses."""

    def __init__(self, *responses: httpx.Response) -> None:
        self.responses = list(responses)
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if len(self.responses) > 1:
            return self.responses.pop(0)
        return self.responses[0]

    def payloads(self) -> list[dict[str, Any]]:
        return [json.loads(request.content) for request in self.requests]


class TokenStub:
    def __init__(self, token: str = "access-token", *, error: Exception | None = None) -> None:
        self.token = token
        self.error = error
        self.users: list[str | None] = []

    async def __call__(self, user_id: str | None) -> str:
        self.users.append(user_id)
        if self.error is not None:
            raise self.error
        return self.token


def _entry(
    *,
    eaten_at: datetime = EATEN_AT,
    meal_type: MealType = MealType.LUNCH,
    items: list[FoodItem] | None = None,
) -> NutritionEntry:
    return NutritionEntry(
        eaten_at=eaten_at,
        meal_type=meal_type,
        source="text",
        items=items or [FoodItem(name="котлета", grams=150)],
    )


def _created(name: str = "users/me/dataTypes/nutrition-log/dataPoints/abc") -> httpx.Response:
    body = {"name": "operations/1", "done": True, "response": {"name": name}}
    return httpx.Response(200, json=body)


Handler = Callable[[httpx.Request], httpx.Response]


@asynccontextmanager
async def _sink(
    tmp_path: Path,
    handler: Handler,
    *,
    token_provider: TokenStub | None = None,
    meal_duration: timedelta = timedelta(minutes=30),
) -> AsyncIterator[GoogleHealthSink]:
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        yield GoogleHealthSink(
            tmp_path / "nutrition.db",
            token_provider=token_provider or TokenStub(),
            meal_duration=meal_duration,
            http_client=client,
        )


async def test_payload_snapshot(tmp_path: Path) -> None:
    recorder = Recorder(_created())
    entry = _entry(
        items=[
            FoodItem(
                name="котлета",
                grams=150,
                kcal=300,
                protein_g=20,
                fat_g=18,
                carbs_g=8,
            )
        ]
    )

    async with _sink(tmp_path, recorder) as sink:
        result = await sink.write(entry, user_id="u-1")

    assert result.items[0].status == "written"
    assert result.items[0].external_id == "users/me/dataTypes/nutrition-log/dataPoints/abc"
    assert recorder.requests[0].url == URL
    assert recorder.requests[0].headers["Authorization"] == "Bearer access-token"
    assert recorder.payloads() == [
        {
            "nutritionLog": {
                "interval": {
                    "startTime": "2026-11-05T12:30:00+00:00",
                    "startUtcOffset": "0s",
                    "endTime": "2026-11-05T13:00:00+00:00",
                    "endUtcOffset": "0s",
                },
                "foodDisplayName": "котлета, 150 г",
                "mealType": "LUNCH",
                "energy": {"kcal": 300.0},
                "totalCarbohydrate": {"grams": 8.0},
                "totalFat": {"grams": 18.0},
                "nutrients": [{"nutrient": "PROTEIN", "quantity": {"grams": 20.0}}],
            }
        }
    ]


async def test_unknown_meal_and_missing_values_are_omitted(tmp_path: Path) -> None:
    recorder = Recorder(_created())
    entry = _entry(meal_type=MealType.UNKNOWN, items=[FoodItem(name="вода")])

    async with _sink(tmp_path, recorder) as sink:
        await sink.write(entry)

    payload = recorder.payloads()[0]["nutritionLog"]
    assert payload["foodDisplayName"] == "вода"
    for absent in ("mealType", "energy", "totalCarbohydrate", "totalFat", "nutrients", "serving"):
        assert absent not in payload


async def test_interval_keeps_eaten_at_offset(tmp_path: Path) -> None:
    recorder = Recorder(_created())
    moscow = datetime(2026, 11, 5, 12, 30, tzinfo=timezone(timedelta(hours=3)))

    async with _sink(tmp_path, recorder) as sink:
        await sink.write(_entry(eaten_at=moscow))

    interval = recorder.payloads()[0]["nutritionLog"]["interval"]
    assert interval["startTime"] == "2026-11-05T12:30:00+03:00"
    assert interval["startUtcOffset"] == "10800s"
    assert interval["endTime"] == "2026-11-05T13:00:00+03:00"
    assert interval["endUtcOffset"] == "10800s"


async def test_401_marks_item_failed_without_raising(tmp_path: Path) -> None:
    recorder = Recorder(httpx.Response(401, text="invalid access token"))

    async with _sink(tmp_path, recorder) as sink:
        result = await sink.write(_entry())

    assert result.items[0].status == "failed"
    assert result.items[0].error == "HTTP 401: invalid access token"


async def test_one_failed_item_does_not_stop_the_rest(tmp_path: Path) -> None:
    recorder = Recorder(_created(), httpx.Response(500, text="boom"), _created())
    entry = _entry(items=[FoodItem(name=f"item-{index}") for index in range(3)])

    async with _sink(tmp_path, recorder) as sink:
        result = await sink.write(entry)

    assert [item.status for item in result.items] == ["written", "failed", "written"]
    assert result.items[1].error == "HTTP 500: boom"
    assert len(recorder.requests) == 3


async def test_repeat_write_skips_known_items_without_http(tmp_path: Path) -> None:
    recorder = Recorder(_created())
    entry = _entry()

    async with _sink(tmp_path, recorder) as sink:
        await sink.write(entry)
        second = await sink.write(entry)

    assert [item.status for item in second.items] == ["skipped"]
    assert len(recorder.requests) == 1


async def test_backfill_posts_only_missing_items(tmp_path: Path) -> None:
    recorder = Recorder(_created(), httpx.Response(500, text="boom"), _created())
    entry = _entry(items=[FoodItem(name="item-0"), FoodItem(name="item-1")])

    async with _sink(tmp_path, recorder) as sink:
        first = await sink.write(entry)
        second = await sink.write(entry)

    assert [item.status for item in first.items] == ["written", "failed"]
    assert [item.status for item in second.items] == ["skipped", "written"]
    assert [payload["nutritionLog"]["foodDisplayName"] for payload in recorder.payloads()] == [
        "item-0",
        "item-1",
        "item-1",
    ]


async def test_token_provider_receives_user_id(tmp_path: Path) -> None:
    recorder = Recorder(_created())
    tokens = TokenStub()

    async with _sink(tmp_path, recorder, token_provider=tokens) as sink:
        await sink.write(_entry(), user_id="u-1")

    assert tokens.users == ["u-1"]


async def test_token_failure_fails_items_without_http(tmp_path: Path) -> None:
    recorder = Recorder(_created())
    tokens = TokenStub(error=RuntimeError("refresh token expired"))

    async with _sink(tmp_path, recorder, token_provider=tokens) as sink:
        result = await sink.write(_entry())

    assert result.items[0].status == "failed"
    assert result.items[0].error == "token provider failed: refresh token expired"
    assert recorder.requests == []


def test_google_sink_satisfies_protocol(tmp_path: Path) -> None:
    sink: Sink = GoogleHealthSink(tmp_path / "nutrition.db", token_provider=TokenStub())

    assert isinstance(sink, Sink)
    assert sink.name == "google_health"
