"""Tests for :mod:`nutrition_agent.routing`."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest

from nutrition_agent.llm import ImagePart, TextPart
from nutrition_agent.routing import (
    MAX_AUDIO_BYTES,
    MAX_IMAGE_BYTES,
    PreparedInput,
    detect_image_mime,
    prepare_input,
)

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16


@pytest.mark.parametrize(
    ("payload", "status", "source"),
    [
        ({}, "invalid_input", None),
        ({"text": "  "}, "invalid_input", None),
        ({"text": "борщ"}, "ok", "text"),
        ({"image_bytes": PNG}, "ok", "image"),
        ({"text": "это обед", "image_bytes": PNG}, "ok", "mixed"),
        ({"audio_bytes": b"ogg", "transcript": "две котлеты"}, "ok", "audio"),
        ({"text": "обед", "audio_bytes": b"ogg", "transcript": "две котлеты"}, "ok", "mixed"),
        ({"audio_bytes": b"ogg"}, "invalid_input", None),
        ({"transcript": "две котлеты"}, "invalid_input", None),
        ({"audio_bytes": b"ogg", "image_bytes": PNG, "transcript": "x"}, "invalid_input", None),
        (
            {"text": "фото", "audio_bytes": b"ogg", "image_bytes": PNG, "transcript": "x"},
            "invalid_input",
            None,
        ),
    ],
)
def test_input_combinations(payload: dict[str, object], status: str, source: str | None) -> None:
    prepared = prepare_input(**payload)  # type: ignore[arg-type]

    assert prepared.status == status
    if status == "ok":
        assert prepared.source == source
        assert prepared.content
        assert prepared.eaten_at is not None
        assert prepared.error is None
    else:
        assert prepared.source is None
        assert prepared.content is None
        assert prepared.eaten_at is None
        assert prepared.error


def test_text_and_image_content_carries_both_parts() -> None:
    prepared = prepare_input(text="это мой обед", image_bytes=PNG)

    assert isinstance(prepared.content, list)
    assert prepared.content == [TextPart("это мой обед"), ImagePart(PNG, "image/png")]


def test_text_and_transcript_are_joined() -> None:
    prepared = prepare_input(text="без масла", audio_bytes=b"ogg", transcript="две котлеты")

    assert prepared.content == "две котлеты\n\nбез масла"


def test_transcript_without_text_is_the_content() -> None:
    prepared = prepare_input(audio_bytes=b"ogg", transcript="две котлеты")

    assert prepared.content == "две котлеты"


@pytest.mark.parametrize(
    ("prefix", "mime"),
    [
        (b"\x89PNG\r\n\x1a\n", "image/png"),
        (b"\xff\xd8\xff\xe0", "image/jpeg"),
        (b"GIF87a", "image/gif"),
        (b"GIF89a", "image/gif"),
        (b"RIFF\x00\x00\x00\x00WEBP", "image/webp"),
    ],
)
def test_known_image_signatures(prefix: bytes, mime: str) -> None:
    assert detect_image_mime(prefix + b"\x00" * 8) == mime


@pytest.mark.parametrize(
    "data",
    [b"", b"not an image", b"RIFF\x00\x00\x00\x00WEB", b"\x89PNG\r\n"],
)
def test_unknown_image_signatures(data: bytes) -> None:
    assert detect_image_mime(data) is None


def test_unknown_image_format_is_invalid() -> None:
    prepared = prepare_input(image_bytes=b"arbitrary bytes")

    assert prepared.status == "invalid_input"
    assert prepared.error == "unsupported image format"


def test_audio_limit_boundary() -> None:
    assert prepare_input(audio_bytes=b"\x00" * MAX_AUDIO_BYTES, transcript="x").status == "ok"
    prepared = prepare_input(audio_bytes=b"\x00" * (MAX_AUDIO_BYTES + 1), transcript="x")

    assert prepared.status == "invalid_input"
    assert prepared.error is not None
    assert "audio exceeds" in prepared.error


def test_image_limit_boundary() -> None:
    image = PNG + b"\x00" * (MAX_IMAGE_BYTES - len(PNG) + 1)

    prepared = prepare_input(image_bytes=image)

    assert prepared.status == "invalid_input"
    assert prepared.error is not None
    assert "image exceeds" in prepared.error


def test_image_at_exact_limit_is_accepted() -> None:
    image = PNG + b"\x00" * (MAX_IMAGE_BYTES - len(PNG))

    assert prepare_input(image_bytes=image).status == "ok"


def test_naive_eaten_at_is_invalid() -> None:
    prepared = prepare_input(text="борщ", eaten_at=datetime(2026, 11, 5, 12, 30))

    assert prepared.status == "invalid_input"
    assert prepared.error == "eaten_at must be timezone-aware"


def test_aware_eaten_at_is_preserved() -> None:
    moscow = datetime(2026, 11, 5, 12, 30, tzinfo=timezone(timedelta(hours=3)))

    assert prepare_input(text="борщ", eaten_at=moscow).eaten_at == moscow


def test_missing_eaten_at_defaults_to_now_in_utc() -> None:
    before = datetime.now(UTC)

    prepared = prepare_input(text="борщ")

    assert prepared.eaten_at is not None
    assert prepared.eaten_at.tzinfo is UTC
    assert before <= prepared.eaten_at <= datetime.now(UTC)


def test_ok_prepared_input_requires_content() -> None:
    with pytest.raises(ValueError, match="ok input is missing"):
        PreparedInput(status="ok")
