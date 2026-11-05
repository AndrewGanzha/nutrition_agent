"""Tests for :mod:`nutrition_agent.extraction`."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any, cast

import pytest

from nutrition_agent.domain import MealType
from nutrition_agent.extraction import (
    EXTRACTION_SCHEMA,
    EXTRACTION_SCHEMA_NAME,
    ExtractedMeal,
    extract_meal,
)
from nutrition_agent.llm import ImagePart, Messages, TextPart

EATEN_AT = datetime(2026, 11, 5, 12, 30, tzinfo=UTC)

VALID_MEAL: dict[str, object] = {
    "is_meal": True,
    "meal_type": "lunch",
    "items": [
        {
            "name": "котлета",
            "grams": 150,
            "kcal": 300,
            "protein_g": 20,
            "fat_g": 18,
            "carbs_g": 8,
            "confidence": 0.7,
        }
    ],
    "notes": "",
}


class ScriptedLLM:
    """LLM stub returning canned answers and recording every call."""

    def __init__(self, answers: list[str]) -> None:
        self.answers = list(answers)
        self.calls: list[tuple[Messages, dict[str, object] | None, str]] = []

    async def complete(
        self,
        messages: Messages,
        *,
        schema: dict[str, object] | None = None,
        schema_name: str = "result",
    ) -> str:
        self.calls.append((list(messages), schema, schema_name))
        return self.answers.pop(0)


def _json(payload: object) -> str:
    return json.dumps(payload, ensure_ascii=False)


async def test_valid_json_becomes_entry() -> None:
    llm = ScriptedLLM([_json(VALID_MEAL)])

    result = await extract_meal(llm, content="котлета и гречка", eaten_at=EATEN_AT, source="text")

    assert result.status == "ok"
    assert result.entry is not None
    assert result.entry.eaten_at == EATEN_AT
    assert result.entry.source == "text"
    assert result.entry.meal_type is MealType.LUNCH
    assert [item.name for item in result.entry.items] == ["котлета"]
    assert result.entry.notes is None

    messages, schema, schema_name = llm.calls[0]
    assert schema == EXTRACTION_SCHEMA
    assert schema_name == EXTRACTION_SCHEMA_NAME
    assert messages[0].role == "system"
    assert messages[1].role == "user"
    assert messages[1].content == "котлета и гречка"


async def test_non_meal_is_ignored() -> None:
    payload = {**VALID_MEAL, "is_meal": False, "items": []}
    llm = ScriptedLLM([_json(payload)])

    result = await extract_meal(llm, content="спасибо!", eaten_at=EATEN_AT, source="audio")

    assert result.status == "ignored"
    assert result.entry is None
    assert len(llm.calls) == 1


async def test_invalid_json_gets_exactly_one_repair() -> None:
    llm = ScriptedLLM(["не JSON", _json(VALID_MEAL)])

    result = await extract_meal(llm, content="котлета", eaten_at=EATEN_AT, source="text")

    assert result.status == "ok"
    assert len(llm.calls) == 2
    repair_messages, schema, _ = llm.calls[1]
    assert schema == EXTRACTION_SCHEMA
    assert [message.role for message in repair_messages] == ["system", "user", "assistant", "user"]
    assert repair_messages[2].content == "не JSON"
    repair_text = repair_messages[3].content
    assert isinstance(repair_text, str)
    assert "не прошёл валидацию" in repair_text
    assert "invalid JSON" in repair_text


async def test_invalid_schema_payload_gets_one_repair() -> None:
    broken = json.dumps({**VALID_MEAL, "items": [{"name": "", "grams": 100}]})
    llm = ScriptedLLM([broken, _json(VALID_MEAL)])

    result = await extract_meal(llm, content="котлета", eaten_at=EATEN_AT, source="text")

    assert result.status == "ok"
    assert len(llm.calls) == 2
    repair_text = llm.calls[1][0][3].content
    assert isinstance(repair_text, str)
    assert "name" in repair_text


async def test_failed_repair_returns_parse_failed_with_raw_response() -> None:
    second_raw = "тоже не JSON"
    llm = ScriptedLLM(["не JSON", second_raw])

    result = await extract_meal(llm, content="котлета", eaten_at=EATEN_AT, source="text")

    assert result.status == "parse_failed"
    assert result.entry is None
    assert result.raw_response == second_raw
    assert result.error
    assert len(llm.calls) == 2


async def test_is_meal_true_without_items_is_invalid() -> None:
    with pytest.raises(ValueError, match="items must not be empty"):
        ExtractedMeal.model_validate({"is_meal": True, "items": []})


async def test_multimodal_content_reaches_the_user_message() -> None:
    content: list[TextPart | ImagePart] = [
        TextPart("что на фото?"),
        ImagePart(data=b"\x89PNG", mime_type="image/png"),
    ]
    llm = ScriptedLLM([_json(VALID_MEAL)])

    await extract_meal(llm, content=content, eaten_at=EATEN_AT, source="mixed")

    assert llm.calls[0][0][1].content == content


def test_schema_is_strict_for_every_object() -> None:
    properties = cast(dict[str, Any], EXTRACTION_SCHEMA["properties"])
    item_schema = cast(dict[str, Any], properties["items"]["items"])

    assert EXTRACTION_SCHEMA["additionalProperties"] is False
    assert sorted(cast(list[str], EXTRACTION_SCHEMA["required"])) == sorted(properties)
    assert item_schema["additionalProperties"] is False
    assert sorted(cast(list[str], item_schema["required"])) == sorted(item_schema["properties"])
    assert properties["meal_type"]["enum"] == [meal.value for meal in MealType]
