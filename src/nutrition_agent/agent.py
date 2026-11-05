"""Orchestration: routing → extraction → sink dispatch."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import uuid4

from nutrition_agent.config import AgentConfig
from nutrition_agent.domain import NutritionEntry
from nutrition_agent.extraction import extract_meal
from nutrition_agent.llm import LLMClient, LLMError
from nutrition_agent.results import ProcessResult, ProcessStatus
from nutrition_agent.routing import MAX_AUDIO_BYTES, prepare_input
from nutrition_agent.sinks import ItemWrite, Sink, SinkResult
from nutrition_agent.speech import Transcriber

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _PendingCommit:
    entry: NutritionEntry
    user_id: str | None
    expires_at: datetime


class NutritionAgent:
    """Turns text/audio/photo input into entries and writes them to sinks."""

    def __init__(
        self,
        config: AgentConfig,
        *,
        llm: LLMClient,
        transcriber: Transcriber | None = None,
        sinks: list[Sink],
    ) -> None:
        self._config = config
        self._llm = llm
        self._transcriber = transcriber
        self._sinks = _active_sinks(sinks, config.sink_order)
        self._pending: dict[str, _PendingCommit] = {}

    async def process(
        self,
        *,
        text: str | None = None,
        audio_bytes: bytes | None = None,
        image_bytes: bytes | None = None,
        eaten_at: datetime | None = None,
        dry_run: bool = False,
        user_id: str | None = None,
    ) -> ProcessResult:
        transcript: str | None = None
        if audio_bytes is not None:
            if len(audio_bytes) > MAX_AUDIO_BYTES:
                return ProcessResult(
                    status="invalid_input", error=f"audio exceeds {MAX_AUDIO_BYTES} bytes"
                )
            if self._transcriber is None:
                return ProcessResult(
                    status="invalid_input", error="audio input requires a transcriber"
                )
            transcript = await self._transcriber.transcribe(audio_bytes)
            if not transcript.strip():
                return ProcessResult(status="ignored", transcript=transcript)

        prepared = prepare_input(
            text=text,
            audio_bytes=audio_bytes,
            image_bytes=image_bytes,
            transcript=transcript,
            eaten_at=eaten_at,
        )
        if prepared.status == "invalid_input":
            return ProcessResult(
                status="invalid_input", transcript=transcript, error=prepared.error
            )
        assert prepared.eaten_at is not None  # ok invariants live in PreparedInput
        assert prepared.source is not None
        assert prepared.content is not None

        try:
            extraction = await extract_meal(
                self._llm,
                content=prepared.content,
                eaten_at=prepared.eaten_at,
                source=prepared.source,
            )
        except LLMError as exc:
            return ProcessResult(status="error", transcript=transcript, error=str(exc))

        raw_response = (
            extraction.raw_response
            if (self._config.debug or extraction.status == "parse_failed")
            else None
        )

        if extraction.status == "ignored":
            return ProcessResult(status="ignored", transcript=transcript, raw_response=raw_response)
        if extraction.entry is None:
            return ProcessResult(
                status="parse_failed",
                transcript=transcript,
                raw_response=raw_response,
                error=extraction.error,
            )

        return await self._deliver(
            extraction.entry,
            user_id,
            dry_run=dry_run,
            transcript=transcript,
            raw_response=raw_response,
        )

    async def write(
        self,
        entry: NutritionEntry,
        *,
        user_id: str | None = None,
        dry_run: bool = False,
    ) -> ProcessResult:
        """Write an already parsed entry, bypassing routing, STT and the LLM.

        Idempotency lives in the sinks: they report ``skipped`` for items they
        already hold (``sink_writes``), so a repeated or resumed write only
        reaches the missing items. The agent never filters items itself.
        """
        return await self._deliver(entry, user_id, dry_run=dry_run)

    async def commit(self, token: str) -> ProcessResult:
        """Write the entry remembered by a dry-run token.

        The token survives ``partial`` and ``error`` results so the caller can
        retry and let the sinks fill in whatever is still missing; only a fully
        successful write spends it. Expired and already spent tokens are errors.
        """
        pending = self._pending.get(token)
        if pending is None:
            return ProcessResult(status="error", error="unknown or already committed token")
        if pending.expires_at <= datetime.now(UTC):
            del self._pending[token]
            return ProcessResult(status="error", error="commit token expired")
        # Consume before awaiting so overlapping commits cannot dispatch twice.
        del self._pending[token]
        self._purge_expired()

        try:
            result = await self._deliver(pending.entry, pending.user_id, dry_run=False)
        except BaseException:
            # Cancellation/timeout must not burn a token it did not spend.
            self._restore(token, pending)
            raise
        if result.status != "ok":
            self._restore(token, pending)
        return result

    def _restore(self, token: str, pending: _PendingCommit) -> None:
        if pending.expires_at > datetime.now(UTC):
            self._pending[token] = pending

    def _purge_expired(self) -> None:
        now = datetime.now(UTC)
        for token in [name for name, pending in self._pending.items() if pending.expires_at <= now]:
            del self._pending[token]

    async def _deliver(
        self,
        entry: NutritionEntry,
        user_id: str | None,
        *,
        dry_run: bool,
        transcript: str | None = None,
        raw_response: str | None = None,
    ) -> ProcessResult:
        if dry_run:
            return ProcessResult(
                status="ok",
                entry=entry,
                transcript=transcript,
                commit_token=self._issue_token(entry, user_id),
                raw_response=raw_response,
            )

        sink_results = await self._dispatch(entry, user_id)
        status = _aggregate_status(sink_results)
        return ProcessResult(
            status=status,
            entry=entry,
            transcript=transcript,
            sink_results=sink_results,
            raw_response=raw_response,
            error=None if status != "error" else "no sink wrote the entry",
        )

    async def _dispatch(self, entry: NutritionEntry, user_id: str | None) -> list[SinkResult]:
        results: list[SinkResult] = []
        for sink in self._sinks:
            started = time.perf_counter()
            try:
                result = await sink.write(entry, user_id=user_id)
            except Exception as exc:  # a broken sink must not stop the chain
                result = SinkResult(
                    name=sink.name,
                    items=[
                        ItemWrite(index=index, status="failed", error=str(exc))
                        for index in range(len(entry.items))
                    ],
                )
            duration_ms = round((time.perf_counter() - started) * 1000, 1)
            logger.info(
                "sink write finished",
                extra={
                    "entry_id": str(entry.id),
                    "sink": sink.name,
                    "user_id": user_id,
                    "duration_ms": duration_ms,
                },
            )
            for item in result.items:
                if item.status == "failed":
                    logger.warning(
                        "sink item failed",
                        extra={
                            "entry_id": str(entry.id),
                            "sink": sink.name,
                            "user_id": user_id,
                            "item_idx": item.index,
                            "duration_ms": duration_ms,
                            "error": item.error,
                        },
                    )
            results.append(result)
        return results

    def _issue_token(self, entry: NutritionEntry, user_id: str | None) -> str:
        self._purge_expired()
        token = str(uuid4())
        self._pending[token] = _PendingCommit(
            entry=entry,
            user_id=user_id,
            expires_at=datetime.now(UTC) + self._config.commit_ttl,
        )
        return token


def _active_sinks(sinks: list[Sink], order: tuple[str, ...]) -> list[Sink]:
    """Return active sinks in configured order; unlisted sinks are inactive."""
    by_name: dict[str, Sink] = {}
    for sink in sinks:
        if sink.name in by_name:
            raise ValueError(f"duplicate sink name: {sink.name!r}")
        by_name[sink.name] = sink
    return [by_name[name] for name in order if name in by_name]


def _aggregate_status(results: list[SinkResult]) -> ProcessStatus:
    if not results:
        return "error"
    statuses = [item.status for result in results for item in result.items]
    if "failed" not in statuses:
        return "ok"
    if "written" in statuses or "skipped" in statuses:
        return "partial"
    return "error"


__all__ = ["NutritionAgent"]
