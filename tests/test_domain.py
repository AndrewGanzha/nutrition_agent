"""Tests for :mod:`nutrition_agent.domain`."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from nutrition_agent.domain import FoodItem, MealType, NutritionEntry

EATEN_AT = datetime(2026, 11, 5, 12, 30, tzinfo=UTC)


def _entry(**overrides: object) -> NutritionEntry:
    payload: dict[str, object] = {
        "eaten_at": EATEN_AT,
        "source": "text",
        "items": [FoodItem(name="гречка", grams=200, kcal=260)],
    }
    payload.update(overrides)
    return NutritionEntry.model_validate(payload)


@pytest.mark.parametrize(
    "eaten_at",
    [
        datetime(2026, 11, 5, 12, 30),
        datetime(2026, 11, 5, 12, 30, tzinfo=timezone(timedelta(hours=3))).replace(tzinfo=None),
    ],
)
def test_naive_eaten_at_rejected(eaten_at: datetime) -> None:
    with pytest.raises(ValidationError):
        _entry(eaten_at=eaten_at)


def test_tz_aware_eaten_at_accepted() -> None:
    moscow = datetime(2026, 11, 5, 12, 30, tzinfo=timezone(timedelta(hours=3)))

    assert _entry(eaten_at=moscow).eaten_at == moscow


def test_entry_requires_at_least_one_item() -> None:
    with pytest.raises(ValidationError):
        _entry(items=[])


@pytest.mark.parametrize("name", ["", "   "])
def test_blank_item_name_rejected(name: str) -> None:
    with pytest.raises(ValidationError):
        FoodItem(name=name)


def test_ids_are_generated_once_and_unique() -> None:
    first = _entry()
    second = _entry()

    assert first.id != second.id
    assert first.id.version == 4
    assert first.id == first.model_copy().id


def test_totals_ignore_unknown_values() -> None:
    entry = _entry(
        items=[
            FoodItem(name="котлета", grams=150, kcal=300, protein_g=20, fat_g=None),
            FoodItem(name="гречка", grams=200, kcal=None, protein_g=6, fat_g=2),
        ]
    )

    assert entry.total_grams == 350
    assert entry.total_kcal == 300
    assert entry.total_protein_g == 26
    assert entry.total_fat_g == 2


def test_totals_are_none_when_no_item_knows_the_value() -> None:
    entry = _entry(items=[FoodItem(name="салат"), FoodItem(name="компот", grams=250)])

    assert entry.total_kcal is None
    assert entry.total_protein_g is None
    assert entry.total_fat_g is None
    assert entry.total_carbs_g is None
    assert entry.total_grams == 250


def test_meal_type_defaults_to_unknown() -> None:
    assert _entry().meal_type is MealType.UNKNOWN


def test_unknown_meal_type_rejected() -> None:
    with pytest.raises(ValidationError):
        _entry(meal_type="brunch")


@pytest.mark.parametrize("field", ["grams", "kcal", "protein_g", "fat_g", "carbs_g"])
def test_negative_nutrients_rejected(field: str) -> None:
    with pytest.raises(ValidationError):
        FoodItem.model_validate({"name": "гречка", field: -1})


@pytest.mark.parametrize("confidence", [-0.1, 1.1])
def test_confidence_outside_unit_interval_rejected(confidence: float) -> None:
    with pytest.raises(ValidationError):
        FoodItem(name="гречка", confidence=confidence)


def test_unknown_fields_rejected() -> None:
    with pytest.raises(ValidationError):
        _entry(meal="обед")
