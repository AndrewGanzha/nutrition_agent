"""Shared pydantic building blocks for the public models."""

from __future__ import annotations

from typing import Annotated

from pydantic import ConfigDict, StringConstraints

NonEmptyStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]

STRICT_MODEL_CONFIG = ConfigDict(frozen=True, extra="forbid")


def blank_to_none(value: str | None) -> str | None:
    """Strip text and collapse blanks to ``None``.

    Models (LLM output in strict mode included) routinely emit ``""`` for
    optional strings; an empty note means "no note", not a validation failure.
    """
    if value is None:
        return None
    return value.strip() or None


__all__ = ["STRICT_MODEL_CONFIG", "NonEmptyStr", "blank_to_none"]
