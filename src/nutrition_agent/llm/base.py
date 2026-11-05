"""LLM contract: messages in, raw JSON text out."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal, Protocol

Messages = list["Message"]


class LLMError(Exception):
    """The LLM call failed (non-retryable error or exhausted attempt budget)."""


@dataclass(frozen=True)
class TextPart:
    text: str


@dataclass(frozen=True)
class ImagePart:
    """Image bytes with the MIME type detected by the caller."""

    data: bytes
    mime_type: str


ContentPart = TextPart | ImagePart


@dataclass(frozen=True)
class Message:
    role: Literal["system", "user", "assistant"]
    content: str | Sequence[ContentPart]


class LLMClient(Protocol):
    async def complete(
        self,
        messages: Messages,
        *,
        schema: dict[str, object] | None = None,
        schema_name: str = "result",
    ) -> str: ...


__all__ = [
    "ContentPart",
    "ImagePart",
    "LLMClient",
    "LLMError",
    "Message",
    "Messages",
    "TextPart",
]
