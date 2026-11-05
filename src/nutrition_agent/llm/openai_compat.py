"""OpenAI-compatible LLM adapter (DeepSeek by default).

Supports both wire shapes the decisions table allows: the Responses API with a
strict JSON schema, and Chat Completions with ``json_object``. Retries are owned
here — the OpenAI SDK's own retries are disabled — so attempt counts and
backoff match ``RetryPolicy`` exactly.
"""

from __future__ import annotations

import asyncio
import base64
from typing import Any, cast

import openai
from openai import AsyncOpenAI
from openai.types.chat import ChatCompletion
from openai.types.responses import Response as ResponsesResponse

from nutrition_agent.config import LLMConfig, RetryPolicy
from nutrition_agent.llm.base import ImagePart, LLMError, Message, Messages


class OpenAICompatibleLLM:
    """``LLMClient`` implementation for DeepSeek/OpenAI-compatible endpoints."""

    def __init__(
        self,
        config: LLMConfig,
        *,
        timeout: float,
        retry: RetryPolicy,
        # openai>=3 ships `httpx2` and types this as `httpx2.AsyncClient`; plain
        # `httpx` clients (MockTransport in tests, custom transports) are
        # runtime-compatible, so the seam stays duck-typed.
        http_client: Any | None = None,
    ) -> None:
        self._config = config
        self._retry = retry
        self._client = AsyncOpenAI(
            base_url=config.base_url,
            api_key=config.api_key or "not-needed",
            timeout=timeout,
            max_retries=0,
            http_client=http_client,
        )

    async def complete(
        self,
        messages: Messages,
        *,
        schema: dict[str, object] | None = None,
        schema_name: str = "result",
    ) -> str:
        payload = self._build_payload(messages, schema, schema_name)
        delay = self._retry.backoff_base
        last_error: Exception | None = None

        for attempt in range(1, self._retry.max_attempts + 1):
            try:
                return self._extract(await self._send(payload))
            except openai.APITimeoutError as exc:
                last_error = exc
            except openai.APIStatusError as exc:
                last_error = exc
                if not _is_retryable_status(exc.status_code):
                    raise LLMError(f"LLM request rejected with HTTP {exc.status_code}") from exc
            except openai.APIConnectionError as exc:
                raise LLMError("LLM endpoint is unreachable") from exc

            if attempt < self._retry.max_attempts:
                await asyncio.sleep(delay)
                delay = min(delay * 2, self._retry.backoff_max)

        raise LLMError(f"LLM request failed after {self._retry.max_attempts} attempts") from (
            last_error
        )

    def _build_payload(
        self,
        messages: Messages,
        schema: dict[str, object] | None,
        schema_name: str,
    ) -> dict[str, Any]:
        model = self._config.vision_model if _contains_image(messages) else self._config.model
        if self._config.api == "responses":
            payload: dict[str, Any] = {
                "model": model,
                "input": [
                    {"role": message.role, "content": _responses_content(message)}
                    for message in messages
                ],
                "reasoning": {"effort": "none"},
            }
            if schema is not None:
                payload["text"] = {
                    "format": {
                        "type": "json_schema",
                        "name": schema_name,
                        "schema": schema,
                        "strict": True,
                    }
                }
            return payload

        payload = {
            "model": model,
            "messages": [
                {"role": message.role, "content": _chat_content(message)} for message in messages
            ],
            "extra_body": {"thinking": {"type": "disabled"}},
            "response_format": {"type": "json_object"},
        }
        return payload

    async def _send(self, payload: dict[str, Any]) -> ResponsesResponse | ChatCompletion:
        if self._config.api == "responses":
            return cast(ResponsesResponse, await self._client.responses.create(**payload))
        return cast(ChatCompletion, await self._client.chat.completions.create(**payload))

    def _extract(self, response: ResponsesResponse | ChatCompletion) -> str:
        if isinstance(response, ChatCompletion):
            return response.choices[0].message.content or ""
        return response.output_text


def _contains_image(messages: Messages) -> bool:
    return any(
        isinstance(part, ImagePart)
        for message in messages
        if not isinstance(message.content, str)
        for part in message.content
    )


def _data_url(part: ImagePart) -> str:
    encoded = base64.b64encode(part.data).decode("ascii")
    return f"data:{part.mime_type};base64,{encoded}"


def _responses_content(message: Message) -> str | list[dict[str, Any]]:
    if isinstance(message.content, str):
        return message.content
    parts: list[dict[str, Any]] = []
    for part in message.content:
        if isinstance(part, ImagePart):
            parts.append({"type": "input_image", "image_url": _data_url(part)})
        else:
            parts.append({"type": "input_text", "text": part.text})
    return parts


def _chat_content(message: Message) -> str | list[dict[str, Any]]:
    if isinstance(message.content, str):
        return message.content
    parts: list[dict[str, Any]] = []
    for part in message.content:
        if isinstance(part, ImagePart):
            parts.append({"type": "image_url", "image_url": {"url": _data_url(part)}})
        else:
            parts.append({"type": "text", "text": part.text})
    return parts


def _is_retryable_status(status_code: int) -> bool:
    return status_code == 429 or status_code >= 500


__all__ = ["OpenAICompatibleLLM"]
