"""Tests for :mod:`nutrition_agent.agent`."""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime, timedelta

import pytest

from nutrition_agent.agent import NutritionAgent
from nutrition_agent.config import AgentConfig
from nutrition_agent.domain import FoodItem, NutritionEntry
from nutrition_agent.llm import LLMError, Messages
from nutrition_agent.sinks import FakeSink, ItemStatus, ItemWrite, Sink, SinkResult

EATEN_AT = datetime(2026, 11, 5, 12, 30, tzinfo=UTC)

MEAL_JSON = json.dumps(
    {
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
        "notes": None,
    },
    ensure_ascii=False,
)
NOT_MEAL_JSON = json.dumps({"is_meal": False, "meal_type": "unknown", "items": [], "notes": None})


class StubLLM:
    def __init__(self, answers: list[str]) -> None:
        self.answers = list(answers)
        self.calls: list[Messages] = []
        self.fails: Exception | None = None

    async def complete(
        self,
        messages: Messages,
        *,
        schema: dict[str, object] | None = None,
        schema_name: str = "result",
    ) -> str:
        self.calls.append(list(messages))
        if self.fails is not None:
            raise self.fails
        return self.answers.pop(0)


class StubTranscriber:
    def __init__(self, text: str) -> None:
        self.text = text
        self.calls: list[bytes] = []

    async def transcribe(self, audio: bytes, *, filename: str = "audio.ogg") -> str:
        self.calls.append(audio)
        return self.text


def _agent(
    llm: StubLLM | None = None,
    *,
    sinks: list[Sink] | None = None,
    transcriber: StubTranscriber | None = None,
    debug: bool = False,
    sink_order: tuple[str, ...] = ("sqlite", "google_health"),
    commit_ttl: timedelta = timedelta(minutes=15),
) -> NutritionAgent:
    return NutritionAgent(
        AgentConfig(sink_order=sink_order, debug=debug, commit_ttl=commit_ttl),
        llm=llm or StubLLM([MEAL_JSON]),
        transcriber=transcriber,
        sinks=sinks if sinks is not None else [FakeSink("sqlite"), FakeSink("google_health")],
    )


async def test_text_input_is_written_to_all_sinks() -> None:
    sqlite, google = FakeSink("sqlite"), FakeSink("google_health")

    result = await _agent(sinks=[google, sqlite]).process(text="две котлеты")

    assert result.status == "ok"
    assert result.entry is not None
    assert result.entry.source == "text"
    assert [item.name for item in result.entry.items] == ["котлета"]
    assert [sink_result.name for sink_result in result.sink_results] == ["sqlite", "google_health"]
    assert [call.entry for call in sqlite.calls] == [result.entry]
    assert [call.entry for call in google.calls] == [result.entry]
    assert result.sinks_succeeded == ["sqlite", "google_health"]
    assert result.raw_response is None


async def test_sinks_not_in_order_are_not_called() -> None:
    extra = FakeSink("fitatu")
    sqlite = FakeSink("sqlite")

    result = await _agent(sinks=[extra, sqlite]).process(text="борщ")

    assert result.status == "ok"
    assert extra.calls == []
    assert result.sinks_succeeded == ["sqlite"]


async def test_raising_sink_does_not_stop_the_chain() -> None:
    sqlite = FakeSink("sqlite")
    google = FakeSink("google_health", raises=RuntimeError("401 token expired"))

    result = await _agent(sinks=[sqlite, google]).process(text="борщ")

    assert result.status == "partial"
    assert len(sqlite.calls) == 1
    assert len(google.calls) == 1
    google_result = result.sink_results[1]
    assert [item.status for item in google_result.items] == ["failed"]
    assert google_result.items[0].error == "401 token expired"


async def test_all_sinks_failing_is_error() -> None:
    sinks: list[Sink] = [
        FakeSink("sqlite", raises=RuntimeError("db locked")),
        FakeSink("google_health", raises=RuntimeError("401")),
    ]

    result = await _agent(sinks=sinks).process(text="борщ")

    assert result.status == "error"
    assert result.entry is not None
    assert result.error == "no sink wrote the entry"
    assert result.sinks_failed == ["sqlite", "google_health"]


async def test_item_level_failure_makes_status_partial() -> None:
    sinks: list[Sink] = [FakeSink("sqlite"), FakeSink("google_health", statuses={0: "failed"})]

    result = await _agent(sinks=sinks).process(text="борщ")

    assert result.status == "partial"
    assert result.sinks_succeeded == ["sqlite"]
    assert result.sinks_failed == ["google_health"]


async def test_dry_run_skips_sinks_and_returns_token() -> None:
    sqlite = FakeSink("sqlite")

    result = await _agent(sinks=[sqlite]).process(text="борщ", dry_run=True)

    assert result.status == "ok"
    assert result.commit_token
    assert sqlite.calls == []
    assert result.sink_results == []


async def test_debug_exposes_raw_response_on_success() -> None:
    quiet = await _agent(debug=False).process(text="борщ")
    loud = await _agent(debug=True).process(text="борщ")

    assert quiet.raw_response is None
    assert loud.raw_response == MEAL_JSON


async def test_parse_failure_exposes_raw_response_without_debug() -> None:
    llm = StubLLM(["не JSON", "тоже не JSON"])

    result = await _agent(llm, sinks=[]).process(text="борщ")

    assert result.status == "parse_failed"
    assert result.raw_response == "тоже не JSON"
    assert result.error
    assert result.entry is None
    assert result.sink_results == []


async def test_llm_failure_is_error_and_skips_sinks() -> None:
    llm = StubLLM([])
    llm.fails = LLMError("boom")
    sqlite = FakeSink("sqlite")

    result = await _agent(llm, sinks=[sqlite]).process(text="борщ")

    assert result.status == "error"
    assert result.error == "boom"
    assert sqlite.calls == []


async def test_missing_input_is_invalid_without_llm_call() -> None:
    llm = StubLLM([MEAL_JSON])

    result = await _agent(llm).process()

    assert result.status == "invalid_input"
    assert result.error
    assert llm.calls == []
    assert result.entry is None


async def test_unknown_image_format_is_invalid() -> None:
    result = await _agent().process(image_bytes=b"definitely not an image")

    assert result.status == "invalid_input"
    assert result.error == "unsupported image format"


async def test_non_meal_is_ignored_and_skips_sinks() -> None:
    sqlite = FakeSink("sqlite")

    result = await _agent(StubLLM([NOT_MEAL_JSON]), sinks=[sqlite]).process(text="спасибо")

    assert result.status == "ignored"
    assert sqlite.calls == []
    assert result.raw_response is None


async def test_audio_requires_transcriber() -> None:
    result = await _agent().process(audio_bytes=b"ogg")

    assert result.status == "invalid_input"
    assert result.error == "audio input requires a transcriber"


async def test_audio_is_transcribed_before_extraction() -> None:
    transcriber = StubTranscriber("две котлеты")
    sqlite = FakeSink("sqlite")

    result = await _agent(transcriber=transcriber, sinks=[sqlite]).process(audio_bytes=b"ogg")

    assert result.status == "ok"
    assert result.transcript == "две котлеты"
    assert transcriber.calls == [b"ogg"]
    assert result.entry is not None
    assert result.entry.source == "audio"
    assert sqlite.calls[0].entry == result.entry


async def test_empty_transcript_is_ignored() -> None:
    transcriber = StubTranscriber("   ")
    llm = StubLLM([MEAL_JSON])

    result = await _agent(llm, transcriber=transcriber).process(audio_bytes=b"ogg")

    assert result.status == "ignored"
    assert result.transcript == "   "
    assert llm.calls == []


async def test_oversized_audio_is_rejected_before_transcription() -> None:
    from nutrition_agent.routing import MAX_AUDIO_BYTES

    transcriber = StubTranscriber("x")

    result = await _agent(transcriber=transcriber).process(
        audio_bytes=b"\x00" * (MAX_AUDIO_BYTES + 1)
    )

    assert result.status == "invalid_input"
    assert transcriber.calls == []


def test_duplicate_sink_names_rejected() -> None:
    with pytest.raises(ValueError, match="duplicate sink name"):
        _agent(sinks=[FakeSink("sqlite"), FakeSink("sqlite")])


async def test_entry_written_once_per_sink() -> None:
    sqlite = FakeSink("sqlite")

    await _agent(sinks=[sqlite]).process(text="борщ")

    assert len(sqlite.calls) == 1
    entry = sqlite.calls[0].entry
    assert isinstance(entry, NutritionEntry)


def _entry() -> NutritionEntry:
    return NutritionEntry(
        eaten_at=EATEN_AT,
        source="text",
        items=[FoodItem(name="котлета", grams=150), FoodItem(name="гречка", grams=200)],
    )


async def test_write_never_calls_llm() -> None:
    llm = StubLLM([])
    sqlite = FakeSink("sqlite")

    result = await _agent(llm, sinks=[sqlite]).write(_entry())

    assert result.status == "ok"
    assert llm.calls == []
    assert result.raw_response is None
    assert result.transcript is None


async def test_write_dispatches_entry_in_sink_order_with_user_id() -> None:
    sqlite, google = FakeSink("sqlite"), FakeSink("google_health")
    entry = _entry()

    result = await _agent(sinks=[google, sqlite]).write(entry, user_id="u-1")

    assert result.entry is entry
    assert [sink_result.name for sink_result in result.sink_results] == ["sqlite", "google_health"]
    assert [call.entry for call in sqlite.calls] == [entry]
    assert [call.user_id for call in google.calls] == ["u-1"]


async def test_repeated_write_treats_sink_skips_as_success() -> None:
    sqlite = FakeSink("sqlite", default_status="skipped")

    result = await _agent(sinks=[sqlite]).write(_entry())

    assert result.status == "ok"
    assert [item.status for item in result.sink_results[0].items] == ["skipped", "skipped"]
    assert result.sinks_succeeded == ["sqlite"]


async def test_write_aggregates_item_level_results() -> None:
    sqlite = FakeSink("sqlite", statuses={1: "skipped"})
    google = FakeSink("google_health", statuses={1: "failed"})

    result = await _agent(sinks=[sqlite, google]).write(_entry())

    assert result.status == "partial"
    assert result.sinks_succeeded == ["sqlite"]
    assert result.sinks_failed == ["google_health"]


async def test_write_all_items_failed_is_error() -> None:
    sqlite = FakeSink("sqlite", default_status="failed")

    result = await _agent(sinks=[sqlite]).write(_entry())

    assert result.status == "error"
    assert result.error == "no sink wrote the entry"


async def test_write_dry_run_skips_sinks_and_returns_token() -> None:
    sqlite = FakeSink("sqlite")

    result = await _agent(sinks=[sqlite]).write(_entry(), dry_run=True)

    assert result.status == "ok"
    assert result.commit_token
    assert result.sink_results == []
    assert sqlite.calls == []


class ToggleSink:
    """Sink that fails every item until ``failing`` is switched off."""

    name = "google_health"

    def __init__(self) -> None:
        self.failing = True
        self.calls: list[tuple[NutritionEntry, str | None]] = []

    async def write(self, entry: NutritionEntry, *, user_id: str | None = None) -> SinkResult:
        self.calls.append((entry, user_id))
        status: ItemStatus = "failed" if self.failing else "written"
        return SinkResult(
            name=self.name,
            items=[
                ItemWrite(
                    index=index,
                    status=status,
                    error="401 token expired" if self.failing else None,
                )
                for index in range(len(entry.items))
            ],
        )


async def test_commit_writes_dry_run_entry_without_llm() -> None:
    llm = StubLLM([MEAL_JSON])
    sqlite = FakeSink("sqlite")
    agent = _agent(llm, sinks=[sqlite])

    preview = await agent.process(text="две котлеты", dry_run=True)
    assert preview.commit_token is not None
    result = await agent.commit(preview.commit_token)

    assert result.status == "ok"
    assert result.entry == preview.entry
    assert [call.entry for call in sqlite.calls] == [preview.entry]
    assert len(llm.calls) == 1


async def test_commit_forwards_user_id_from_token() -> None:
    sqlite = FakeSink("sqlite")
    agent = _agent(sinks=[sqlite])

    preview = await agent.process(text="борщ", dry_run=True, user_id="u-1")
    assert preview.commit_token is not None
    await agent.commit(preview.commit_token)

    assert [call.user_id for call in sqlite.calls] == ["u-1"]


async def test_partial_commit_keeps_token_until_success() -> None:
    sqlite = FakeSink("sqlite")
    google = ToggleSink()
    agent = _agent(sinks=[sqlite, google])

    preview = await agent.process(text="борщ", dry_run=True)
    token = preview.commit_token
    assert token is not None

    first = await agent.commit(token)
    assert first.status == "partial"
    assert len(google.calls) == 1

    google.failing = False
    second = await agent.commit(token)
    assert second.status == "ok"
    assert len(google.calls) == 2
    assert len(sqlite.calls) == 2

    third = await agent.commit(token)
    assert third.status == "error"
    assert third.error == "unknown or already committed token"
    assert len(google.calls) == 2


async def test_failed_commit_keeps_token() -> None:
    google = ToggleSink()
    agent = _agent(sinks=[google])

    preview = await agent.process(text="борщ", dry_run=True)
    token = preview.commit_token
    assert token is not None

    first = await agent.commit(token)
    assert first.status == "error"

    google.failing = False
    second = await agent.commit(token)
    assert second.status == "ok"


async def test_expired_token_is_rejected_without_sink_calls() -> None:
    sqlite = FakeSink("sqlite")
    agent = _agent(sinks=[sqlite], commit_ttl=timedelta(microseconds=1))

    preview = await agent.process(text="борщ", dry_run=True)
    token = preview.commit_token
    assert token is not None
    await asyncio.sleep(0.01)

    result = await agent.commit(token)

    assert result.status == "error"
    assert result.error == "commit token expired"
    assert sqlite.calls == []


async def test_unknown_token_is_error() -> None:
    result = await _agent().commit("no-such-token")

    assert result.status == "error"
    assert result.error == "unknown or already committed token"


def _extra(record: logging.LogRecord) -> dict[str, object]:
    return record.__dict__


async def test_partial_write_logs_one_warning_per_failed_item(
    caplog: pytest.LogCaptureFixture,
) -> None:
    google = FakeSink("google_health", statuses={1: "failed"})
    agent = _agent(sinks=[FakeSink("sqlite"), google])
    entry = _entry()

    with caplog.at_level(logging.WARNING, logger="nutrition_agent.agent"):
        result = await agent.write(entry, user_id="u-1")

    assert result.status == "partial"
    warnings = [record for record in caplog.records if record.getMessage() == "sink item failed"]
    assert len(warnings) == 1
    extra = _extra(warnings[0])
    assert extra["entry_id"] == str(entry.id)
    assert extra["sink"] == "google_health"
    assert extra["user_id"] == "u-1"
    assert extra["item_idx"] == 1
    assert extra["error"] == "fake failure"
    assert isinstance(extra["duration_ms"], float)


async def test_sink_completion_is_logged_with_duration(caplog: pytest.LogCaptureFixture) -> None:
    entry = _entry()

    with caplog.at_level(logging.INFO, logger="nutrition_agent.agent"):
        await _agent(sinks=[FakeSink("sqlite")]).write(entry, user_id="u-1")

    finished = [
        record for record in caplog.records if record.getMessage() == "sink write finished"
    ]
    assert len(finished) == 1
    extra = _extra(finished[0])
    assert extra["entry_id"] == str(entry.id)
    assert extra["sink"] == "sqlite"
    assert extra["user_id"] == "u-1"
    assert isinstance(extra["duration_ms"], float)


async def test_raw_response_is_never_logged(caplog: pytest.LogCaptureFixture) -> None:
    raw = '{"is_meal": true, "items": [], "raw_marker": "RAW-MARKER"}'
    llm = StubLLM([raw, raw])

    with caplog.at_level(logging.DEBUG):
        failed = await _agent(llm, sinks=[], debug=True).process(text="борщ")
        noisy = await _agent(
            StubLLM([MEAL_JSON]),
            sinks=[FakeSink("sqlite", raises=RuntimeError("boom"))],
            debug=True,
        ).process(text="борщ")

    assert failed.status == "parse_failed"
    assert failed.raw_response == raw
    assert noisy.raw_response == MEAL_JSON
    assert caplog.records  # the dispatch log path really ran
    assert "RAW-MARKER" not in caplog.text
    assert all("RAW-MARKER" not in repr(record.__dict__) for record in caplog.records)


class GatedSink:
    """First write parks on a gate so two commits can overlap."""

    name = "sqlite"

    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.calls = 0

    async def write(self, entry: NutritionEntry, *, user_id: str | None = None) -> SinkResult:
        self.calls += 1
        if self.calls == 1:
            self.started.set()
            await self.release.wait()
        return SinkResult(
            name=self.name,
            items=[ItemWrite(index=index, status="written") for index in range(len(entry.items))],
        )


async def test_concurrent_commit_dispatches_once() -> None:
    sink = GatedSink()
    agent = _agent(sinks=[sink])

    preview = await agent.process(text="борщ", dry_run=True)
    token = preview.commit_token
    assert token is not None

    first = asyncio.create_task(agent.commit(token))
    await sink.started.wait()
    second = await agent.commit(token)
    assert second.status == "error"
    assert second.error == "unknown or already committed token"

    sink.release.set()
    assert (await first).status == "ok"
    assert sink.calls == 1


async def test_cancelled_commit_keeps_token() -> None:
    sink = GatedSink()
    agent = _agent(sinks=[sink])

    preview = await agent.process(text="борщ", dry_run=True)
    token = preview.commit_token
    assert token is not None

    task = asyncio.create_task(agent.commit(token))
    await sink.started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    sink.release.set()
    retried = await agent.commit(token)

    assert retried.status == "ok"
    assert sink.calls == 2
