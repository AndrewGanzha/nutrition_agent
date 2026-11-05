"""Meal extraction: prompt, strict JSON schema, and one-shot repair.

The LLM is asked for a strict JSON object. Its text is validated into
:class:`ExtractedMeal`; a failure triggers exactly one repair request that shows
the model the validation error. A second failure is a ``parse_failed`` outcome
with the raw text preserved for diagnostics.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Self

from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator

from nutrition_agent._types import STRICT_MODEL_CONFIG, blank_to_none
from nutrition_agent.domain import EntrySource, FoodItem, MealType, NutritionEntry
from nutrition_agent.llm import (
    ContentPart,
    LLMClient,
    Message,
    Messages,
)

ExtractionStatus = Literal["ok", "ignored", "parse_failed"]

EXTRACTION_SCHEMA_NAME = "meal"

EXTRACTION_EXAMPLE = """{
  "is_meal": true,
  "meal_type": "lunch",
  "items": [
    {"name": "котлета", "grams": 150, "kcal": 300,
     "protein_g": 20, "fat_g": 18, "carbs_g": 8, "confidence": 0.7},
    {"name": "гречка", "grams": 200, "kcal": 260,
     "protein_g": 8, "fat_g": 2, "carbs_g": 50, "confidence": 0.7}
  ],
  "notes": null
}"""

SYSTEM_PROMPT = f"""Ты извлекаешь данные о питании из текста, фото или расшифровки
голосового сообщения.

Правила:
- Если во входе нет еды (благодарность, болтовня, вопрос, пустое сообщение),
  верни is_meal=false и пустой items.
- Один item — одно блюдо или продукт. Состав блюда не дроби, если пользователь
  не назвал его отдельно.
- Значения, которых нет во входе и которые нельзя разумно оценить, оставляй null.
  Не подставляй 0 вместо неизвестного.
- grams — вес порции в граммах; kcal — калорийность порции;
  protein_g, fat_g, carbs_g — БЖУ порции в граммах.
- Оценивай типичные порции, если вес не назван, и снижай confidence.
- meal_type: breakfast, lunch, dinner, snack или unknown (если по контексту неясно).
- confidence — уверенность в оценке от 0 до 1.
- notes — короткие уточнения из входа («без масла», «домашнее»), иначе null.
- Ответ — только JSON по схеме, без markdown и пояснений.

Пример ответа:
{EXTRACTION_EXAMPLE}
"""


_NULLABLE_NUMBER = {"type": ["number", "null"]}

_ITEM_SCHEMA: dict[str, object] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "name",
        "grams",
        "kcal",
        "protein_g",
        "fat_g",
        "carbs_g",
        "confidence",
    ],
    "properties": {
        "name": {"type": "string"},
        "grams": _NULLABLE_NUMBER,
        "kcal": _NULLABLE_NUMBER,
        "protein_g": _NULLABLE_NUMBER,
        "fat_g": _NULLABLE_NUMBER,
        "carbs_g": _NULLABLE_NUMBER,
        "confidence": _NULLABLE_NUMBER,
    },
}

EXTRACTION_SCHEMA: dict[str, object] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["is_meal", "meal_type", "items", "notes"],
    "properties": {
        "is_meal": {"type": "boolean"},
        "meal_type": {"type": "string", "enum": [meal.value for meal in MealType]},
        "items": {"type": "array", "items": _ITEM_SCHEMA},
        "notes": {"type": ["string", "null"]},
    },
}


class ExtractedMeal(BaseModel):
    """Shape of the LLM answer, before it becomes a :class:`NutritionEntry`."""

    model_config = STRICT_MODEL_CONFIG

    is_meal: bool
    meal_type: MealType = MealType.UNKNOWN
    items: list[FoodItem] = Field(default_factory=list)
    notes: str | None = None

    @field_validator("notes")
    @classmethod
    def _blank_notes_to_none(cls, value: str | None) -> str | None:
        return blank_to_none(value)

    @model_validator(mode="after")
    def _meal_needs_items(self) -> Self:
        if self.is_meal and not self.items:
            raise ValueError("items must not be empty when is_meal is true")
        return self


@dataclass(frozen=True)
class ExtractionResult:
    """Outcome of extraction, before any sink is involved."""

    status: ExtractionStatus
    entry: NutritionEntry | None = None
    raw_response: str | None = None
    error: str | None = None


async def extract_meal(
    llm: LLMClient,
    *,
    content: str | list[ContentPart],
    eaten_at: datetime,
    source: EntrySource,
) -> ExtractionResult:
    """Ask the LLM for a meal, repairing a single invalid answer."""
    messages: Messages = [
        Message("system", SYSTEM_PROMPT),
        Message("user", content),
    ]
    raw = await llm.complete(messages, schema=EXTRACTION_SCHEMA, schema_name=EXTRACTION_SCHEMA_NAME)
    meal, error = _parse(raw)

    if meal is None:
        messages = [
            *messages,
            Message("assistant", raw),
            Message("user", repair_prompt(error or "invalid JSON")),
        ]
        raw = await llm.complete(
            messages, schema=EXTRACTION_SCHEMA, schema_name=EXTRACTION_SCHEMA_NAME
        )
        meal, error = _parse(raw)

    if meal is None:
        return ExtractionResult(status="parse_failed", raw_response=raw, error=error)

    if not meal.is_meal:
        return ExtractionResult(status="ignored", raw_response=raw)

    entry = NutritionEntry(
        eaten_at=eaten_at,
        meal_type=meal.meal_type,
        items=meal.items,
        source=source,
        notes=meal.notes,
    )
    return ExtractionResult(status="ok", entry=entry, raw_response=raw)


def repair_prompt(error: str) -> str:
    return (
        f"Предыдущий ответ не прошёл валидацию: {error}\n"
        "Верни исправленный JSON строго по схеме, без markdown и пояснений."
    )


def _parse(raw: str) -> tuple[ExtractedMeal | None, str | None]:
    try:
        return ExtractedMeal.model_validate(json.loads(raw)), None
    except json.JSONDecodeError as exc:
        return None, f"invalid JSON: {exc.msg} at position {exc.pos}"
    except ValidationError as exc:
        details = "; ".join(
            f"{'.'.join(str(part) for part in error['loc']) or 'payload'}: {error['msg']}"
            for error in exc.errors(include_url=False)
        )
        return None, details


__all__ = [
    "EXTRACTION_EXAMPLE",
    "EXTRACTION_SCHEMA",
    "EXTRACTION_SCHEMA_NAME",
    "SYSTEM_PROMPT",
    "ExtractedMeal",
    "ExtractionResult",
    "ExtractionStatus",
    "extract_meal",
    "repair_prompt",
]
