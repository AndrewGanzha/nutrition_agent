"""Tests for :mod:`nutrition_agent.llm.openai_compat` (no network)."""

from __future__ import annotations

import base64
import json
from collections.abc import Callable
from typing import Literal

import httpx
import pytest

from nutrition_agent.config import LLMConfig, RetryPolicy
from nutrition_agent.llm import ImagePart, LLMError, Message, OpenAICompatibleLLM, TextPart

LLMApi = Literal["responses", "chat"]
Handler = Callable[[httpx.Request], httpx.Response]


def _fast_retry(max_attempts: int = 3) -> RetryPolicy:
    return RetryPolicy(max_attempts=max_attempts, backoff_base=0.001, backoff_max=0.002)


def _llm(
    handler: Handler, *, api: LLMApi = "responses", max_attempts: int = 3
) -> OpenAICompatibleLLM:
    return OpenAICompatibleLLM(
        LLMConfig(api=api, api_key="test-key"),
        timeout=5,
        retry=_fast_retry(max_attempts),
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )


def _responses_body(text: str) -> dict[str, object]:
    return {
        "id": "resp_1",
        "object": "response",
        "created_at": 0,
        "status": "completed",
        "model": "deepseek-flash",
        "output": [
            {
                "type": "message",
                "id": "msg_1",
                "status": "completed",
                "role": "assistant",
                "content": [{"type": "output_text", "text": text, "annotations": []}],
            }
        ],
    }


def _chat_body(text: str) -> dict[str, object]:
    return {
        "id": "chatcmpl-1",
        "object": "chat.completion",
        "created": 0,
        "model": "deepseek-flash",
        "choices": [
            {"index": 0, "message": {"role": "assistant", "content": text}, "finish_reason": "stop"}
        ],
    }


def _recorder(bodies: list[httpx.Response], seen: list[dict[str, object]]) -> Handler:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return bodies[0] if len(bodies) == 1 else bodies.pop(0)

    return handler


async def test_responses_request_carries_strict_json_schema() -> None:
    seen: list[dict[str, object]] = []
    llm = _llm(_recorder([httpx.Response(200, json=_responses_body('{"ok": true}'))], seen))
    schema: dict[str, object] = {"type": "object", "properties": {"ok": {"type": "boolean"}}}

    text = await llm.complete([Message("user", "привет")], schema=schema, schema_name="meal")

    assert text == '{"ok": true}'
    payload = seen[0]
    assert payload["text"] == {
        "format": {"type": "json_schema", "name": "meal", "schema": schema, "strict": True}
    }
    assert payload["reasoning"] == {"effort": "none"}
    assert payload["model"] == "deepseek-flash"
    assert payload["input"] == [{"role": "user", "content": "привет"}]


async def test_chat_request_uses_json_object_without_schema() -> None:
    seen: list[dict[str, object]] = []
    llm = _llm(_recorder([httpx.Response(200, json=_chat_body('{"ok": true}'))], seen), api="chat")

    text = await llm.complete([Message("user", "привет")], schema={"type": "object"})

    assert text == '{"ok": true}'
    payload = seen[0]
    assert payload["response_format"] == {"type": "json_object"}
    assert payload["thinking"] == {"type": "disabled"}
    assert "json_schema" not in json.dumps(payload)
    assert payload["messages"] == [{"role": "user", "content": "привет"}]


async def test_retry_stops_after_max_attempts_on_429() -> None:
    seen: list[dict[str, object]] = []
    body = {"error": {"message": "rate limited", "type": "rate_limit_error"}}
    llm = _llm(_recorder([httpx.Response(429, json=body)], seen), max_attempts=3)

    with pytest.raises(LLMError, match="after 3 attempts"):
        await llm.complete([Message("user", "привет")])

    assert len(seen) == 3


async def test_bad_request_is_not_retried() -> None:
    seen: list[dict[str, object]] = []
    body = {"error": {"message": "bad schema", "type": "invalid_request_error"}}
    llm = _llm(_recorder([httpx.Response(400, json=body)], seen))

    with pytest.raises(LLMError, match="HTTP 400"):
        await llm.complete([Message("user", "привет")])

    assert len(seen) == 1


async def test_timeout_is_retried() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise httpx.TimeoutException("too slow", request=request)

    llm = _llm(handler, max_attempts=2)

    with pytest.raises(LLMError, match="after 2 attempts"):
        await llm.complete([Message("user", "привет")])

    assert calls == 2


@pytest.mark.parametrize("api", ["responses", "chat"])
async def test_image_is_sent_as_data_url_and_uses_vision_model(api: LLMApi) -> None:
    seen: list[dict[str, object]] = []
    image = b"\x89PNG\r\n\x1a\npayload"
    body = _responses_body("{}") if api == "responses" else _chat_body("{}")
    llm = OpenAICompatibleLLM(
        LLMConfig(api=api, api_key="test-key", model="text-model", vision_model="vision-model"),
        timeout=5,
        retry=_fast_retry(),
        http_client=httpx.AsyncClient(
            transport=httpx.MockTransport(_recorder([httpx.Response(200, json=body)], seen))
        ),
    )

    await llm.complete(
        [Message("user", [TextPart("что на фото?"), ImagePart(data=image, mime_type="image/png")])]
    )

    payload = seen[0]
    assert payload["model"] == "vision-model"
    if api == "responses":
        content = payload["input"][0]["content"]  # type: ignore[index]
        assert content[0] == {"type": "input_text", "text": "что на фото?"}
        url = content[1]["image_url"]
    else:
        content = payload["messages"][0]["content"]  # type: ignore[index]
        assert content[0] == {"type": "text", "text": "что на фото?"}
        url = content[1]["image_url"]["url"]
    assert url.startswith("data:image/png;base64,")
    assert base64.b64decode(url.split(",", 1)[1]) == image


async def test_text_only_messages_use_text_model() -> None:
    seen: list[dict[str, object]] = []
    llm = _llm(
        _recorder([httpx.Response(200, json=_chat_body("{}"))], seen),
        api="chat",
        max_attempts=1,
    )

    await llm.complete([Message("user", "котлета")])

    assert seen[0]["model"] == "deepseek-flash"
