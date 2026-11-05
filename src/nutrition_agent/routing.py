"""Input validation and message assembly for the extraction pipeline.

Everything the LLM sees is prepared here: which inputs are allowed together,
size limits, image MIME detection by magic bytes, and the default timestamp.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from nutrition_agent._types import blank_to_none
from nutrition_agent.domain import EntrySource
from nutrition_agent.llm import ContentPart, ImagePart, TextPart

MAX_AUDIO_BYTES = 25 * 1024 * 1024
MAX_IMAGE_BYTES = 20 * 1024 * 1024

InputStatus = Literal["ok", "invalid_input"]
UserContent = str | list[ContentPart]


@dataclass(frozen=True)
class PreparedInput:
    """Result of routing one ``process`` call, ready for extraction."""

    status: InputStatus
    eaten_at: datetime | None = None
    source: EntrySource | None = None
    content: UserContent | None = None
    error: str | None = None

    def __post_init__(self) -> None:
        if self.status == "ok":
            missing = [
                name
                for name, value in (
                    ("eaten_at", self.eaten_at),
                    ("source", self.source),
                    ("content", self.content),
                )
                if value is None
            ]
            if missing:
                raise ValueError(f"ok input is missing: {', '.join(missing)}")
        elif self.error is None:
            raise ValueError("invalid_input is missing an error message")


def prepare_input(
    *,
    text: str | None = None,
    audio_bytes: bytes | None = None,
    image_bytes: bytes | None = None,
    transcript: str | None = None,
    eaten_at: datetime | None = None,
) -> PreparedInput:
    """Validate a process call and build the user content for the LLM.

    ``transcript`` is the speech-to-text result for ``audio_bytes`` (phase 2);
    the routing layer never transcribes on its own.
    """
    if eaten_at is not None and _is_naive(eaten_at):
        return _invalid("eaten_at must be timezone-aware")
    eaten = eaten_at or datetime.now(UTC)

    clean_text = blank_to_none(text)
    clean_transcript = blank_to_none(transcript)

    if clean_transcript is not None and audio_bytes is None:
        return _invalid("transcript requires audio_bytes")
    if audio_bytes is not None and clean_transcript is None:
        return _invalid("audio input requires a transcript")
    if audio_bytes is not None and image_bytes is not None:
        return _invalid("audio and image cannot be combined")
    if not (clean_text or clean_transcript or image_bytes is not None):
        return _invalid("at least one input is required: text, audio or image")

    if audio_bytes is not None and len(audio_bytes) > MAX_AUDIO_BYTES:
        return _invalid(f"audio exceeds {MAX_AUDIO_BYTES} bytes")
    if image_bytes is not None and len(image_bytes) > MAX_IMAGE_BYTES:
        return _invalid(f"image exceeds {MAX_IMAGE_BYTES} bytes")

    image_mime = detect_image_mime(image_bytes) if image_bytes is not None else None
    if image_bytes is not None and image_mime is None:
        return _invalid("unsupported image format")
    image_part = (
        ImagePart(data=image_bytes, mime_type=image_mime)
        if image_bytes is not None and image_mime is not None
        else None
    )

    source = _source(
        has_text=clean_text is not None,
        has_audio=audio_bytes is not None,
        has_image=image_bytes is not None,
    )
    return PreparedInput(
        status="ok",
        eaten_at=eaten,
        source=source,
        content=_content(
            text=clean_text,
            transcript=clean_transcript,
            image_part=image_part,
        ),
    )


def detect_image_mime(data: bytes) -> str | None:
    """Return the image MIME type from magic bytes, or ``None`` if unknown."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def _content(
    *, text: str | None, transcript: str | None, image_part: ImagePart | None
) -> UserContent:
    joined = "\n\n".join(part for part in (transcript, text) if part)
    if image_part is None:
        return joined
    parts: list[ContentPart] = []
    if joined:
        parts.append(TextPart(joined))
    parts.append(image_part)
    return parts


def _source(*, has_text: bool, has_audio: bool, has_image: bool) -> EntrySource:
    channels = sum((has_text, has_audio, has_image))
    if channels > 1:
        return "mixed"
    if has_audio:
        return "audio"
    if has_image:
        return "image"
    return "text"


def _is_naive(value: datetime) -> bool:
    return value.tzinfo is None or value.utcoffset() is None


def _invalid(error: str) -> PreparedInput:
    return PreparedInput(status="invalid_input", error=error)


__all__ = [
    "MAX_AUDIO_BYTES",
    "MAX_IMAGE_BYTES",
    "InputStatus",
    "PreparedInput",
    "UserContent",
    "detect_image_mime",
    "prepare_input",
]
