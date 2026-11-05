"""Shared pydantic building blocks for the public models."""

from __future__ import annotations

from typing import Annotated

from pydantic import ConfigDict, StringConstraints

NonEmptyStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]

STRICT_MODEL_CONFIG = ConfigDict(frozen=True, extra="forbid")

__all__ = ["STRICT_MODEL_CONFIG", "NonEmptyStr"]
