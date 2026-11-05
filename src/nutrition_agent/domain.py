"""Domain models: a single meal and its food items.

Optional numeric fields stay ``None`` when the source (LLM, user, backfill
payload) did not provide them. Totals are therefore ``None`` as well when no
item carries the value, so "unknown" never degrades into a fake zero.
"""

from __future__ import annotations

from collections.abc import Iterable
from enum import StrEnum
from typing import Annotated, Literal
from uuid import uuid4

from pydantic import (
    UUID4,
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
)

NonEmptyStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
NonNegative = Annotated[float, Field(ge=0, allow_inf_nan=False)]

_STRICT = ConfigDict(frozen=True, extra="forbid")


class MealType(StrEnum):
    BREAKFAST = "breakfast"
    LUNCH = "lunch"
    DINNER = "dinner"
    SNACK = "snack"
    UNKNOWN = "unknown"


class FoodItem(BaseModel):
    """One dish or ingredient inside a meal."""

    model_config = _STRICT

    name: NonEmptyStr
    grams: NonNegative | None = None
    kcal: NonNegative | None = None
    protein_g: NonNegative | None = None
    fat_g: NonNegative | None = None
    carbs_g: NonNegative | None = None
    confidence: Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)] | None = None


class NutritionEntry(BaseModel):
    """One meal: what was eaten, when, and how it was parsed."""

    model_config = _STRICT

    id: UUID4 = Field(default_factory=uuid4)
    eaten_at: AwareDatetime
    meal_type: MealType = MealType.UNKNOWN
    items: list[FoodItem] = Field(min_length=1)
    source: Literal["text", "audio", "image", "mixed"]
    notes: NonEmptyStr | None = None

    @property
    def total_grams(self) -> float | None:
        return _sum_optional(item.grams for item in self.items)

    @property
    def total_kcal(self) -> float | None:
        return _sum_optional(item.kcal for item in self.items)

    @property
    def total_protein_g(self) -> float | None:
        return _sum_optional(item.protein_g for item in self.items)

    @property
    def total_fat_g(self) -> float | None:
        return _sum_optional(item.fat_g for item in self.items)

    @property
    def total_carbs_g(self) -> float | None:
        return _sum_optional(item.carbs_g for item in self.items)


def _sum_optional(values: Iterable[float | None]) -> float | None:
    """Sum the known values only; return ``None`` when none are known."""
    present = [value for value in values if value is not None]
    return sum(present) if present else None


__all__ = ["FoodItem", "MealType", "NutritionEntry"]
