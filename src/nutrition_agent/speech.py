"""Speech-to-text contract (phase 2 implementations live elsewhere)."""

from __future__ import annotations

from typing import Protocol


class Transcriber(Protocol):
    async def transcribe(self, audio: bytes, *, filename: str = "audio.ogg") -> str: ...


__all__ = ["Transcriber"]
