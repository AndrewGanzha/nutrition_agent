"""End-to-end offline pipeline: agent + real sinks on a temporary SQLite file."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from nutrition_agent import (
    AgentConfig,
    GoogleHealthSink,
    LocalSQLiteSink,
    Message,
    NutritionAgent,
)

EATEN_AT = datetime(2026, 11, 5, 12, 30, tzinfo=UTC)

MEAL_JSON = json.dumps(
    {
        "is_meal": True,
        "meal_type": "lunch",
        "items": [
            {"name": "котлета", "grams": 150, "kcal": 300, "protein_g": 20},
            {"name": "гречка", "grams": 200, "kcal": 260, "carbs_g": 40},
        ],
        "notes": None,
    },
    ensure_ascii=False,
)


class StubLLM:
    def __init__(self, answer: str) -> None:
        self.answer = answer
        self.calls = 0

    async def complete(
        self,
        messages: list[Message],
        *,
        schema: dict[str, object] | None = None,
        schema_name: str = "result",
    ) -> str:
        self.calls += 1
        return self.answer


class TokenStub:
    async def __call__(self, user_id: str | None) -> str:
        return "access-token"


def _counts(path: Path) -> tuple[int, int, int]:
    with sqlite3.connect(path) as db:
        return tuple(
            db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in ("entries", "items", "sink_writes")
        )


async def test_full_text_pipeline_writes_both_sinks_once(tmp_path: Path) -> None:
    posted: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        posted.append(json.loads(request.content))
        return httpx.Response(200, json={"name": "operations/1", "done": True})

    db = tmp_path / "nutrition.db"
    llm = StubLLM(MEAL_JSON)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        agent = NutritionAgent(
            AgentConfig(sink_order=("sqlite", "google_health")),
            llm=llm,
            sinks=[
                LocalSQLiteSink(db),
                GoogleHealthSink(db, token_provider=TokenStub(), http_client=client),
            ],
        )

        result = await agent.process(text="две котлеты и гречка", eaten_at=EATEN_AT)

        assert result.status == "ok"
        assert result.entry is not None
        assert [sink_result.name for sink_result in result.sink_results] == [
            "sqlite",
            "google_health",
        ]
        assert result.sinks_succeeded == ["sqlite", "google_health"]
        assert len(posted) == 2
        assert [payload["nutritionLog"]["foodDisplayName"] for payload in posted] == [
            "котлета, 150 г",
            "гречка, 200 г",
        ]
        assert _counts(db) == (1, 2, 4)

        repeated = await agent.write(result.entry, user_id=None)

        assert repeated.status == "ok"
        assert [item.status for item in repeated.sink_results[0].items] == ["skipped", "skipped"]
        assert [item.status for item in repeated.sink_results[1].items] == ["skipped", "skipped"]
        assert len(posted) == 2
        assert llm.calls == 1
        assert _counts(db) == (1, 2, 4)

    assert await LocalSQLiteSink(db).entries_on(EATEN_AT.date()) == [result.entry]
