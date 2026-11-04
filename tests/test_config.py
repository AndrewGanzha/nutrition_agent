"""Tests for :mod:`nutrition_agent.config`."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

from nutrition_agent.config import AgentConfig, RetryConfig, STTConfig, TimeoutConfig


def test_defaults_follow_fixed_decisions() -> None:
    config = AgentConfig()

    assert config.llm.base_url == "https://api.deepseek.com/v1"
    assert config.llm.model == "deepseek-flash"
    assert config.llm.vision_model == "deepseek-flash"
    assert config.llm.api == "responses"

    assert config.stt.base_url == "https://api.openai.com/v1"
    assert config.stt.model == "whisper-1"
    assert config.stt.language is None
    assert STTConfig(language="ru").language == "ru"

    assert config.timeouts == TimeoutConfig(llm=30.0, stt=60.0, google=10.0, sqlite=5.0)

    assert [config.retry.llm.max_attempts, config.retry.stt.max_attempts] == [3, 2]
    assert [config.retry.google.max_attempts, config.retry.sqlite.max_attempts] == [1, 1]
    assert (config.retry.llm.backoff_base, config.retry.llm.backoff_max) == (1.0, 3.0)

    assert config.sink_order == ("sqlite", "google_health")
    assert config.sqlite_path == Path("nutrition.db")
    assert config.commit_ttl == timedelta(minutes=15)
    assert config.debug is False


@pytest.mark.parametrize(
    "payload",
    [
        {"llm": {"model": ""}},
        {"llm": {"model": "   "}},
        {"llm": {"vision_model": ""}},
        {"llm": {"base_url": ""}},
        {"llm": {"api": "completions"}},
        {"stt": {"model": ""}},
        {"stt": {"language": ""}},
    ],
)
def test_blank_or_unknown_llm_settings_rejected(payload: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        AgentConfig.model_validate(payload)


@pytest.mark.parametrize("component", ["llm", "stt", "google", "sqlite"])
@pytest.mark.parametrize("timeout", [0, -1, -0.5, float("inf")])
def test_non_positive_timeout_rejected(component: str, timeout: float) -> None:
    with pytest.raises(ValidationError):
        AgentConfig.model_validate({"timeouts": {component: timeout}})


def test_retry_attempts_must_be_positive() -> None:
    with pytest.raises(ValidationError):
        RetryConfig.model_validate({"llm": {"max_attempts": 0}})
    with pytest.raises(ValidationError):
        AgentConfig.model_validate({"retry": {"stt": {"max_attempts": -1}}})


def test_backoff_ceiling_below_base_rejected() -> None:
    with pytest.raises(ValidationError):
        AgentConfig.model_validate({"retry": {"llm": {"backoff_base": 5, "backoff_max": 2}}})


@pytest.mark.parametrize("field", ["meal_duration", "commit_ttl"])
@pytest.mark.parametrize("duration", [timedelta(0), timedelta(seconds=-1)])
def test_non_positive_durations_rejected(field: str, duration: timedelta) -> None:
    with pytest.raises(ValidationError):
        AgentConfig.model_validate({field: duration})


@pytest.mark.parametrize(
    "sink_order",
    [(), ("sqlite", "sqlite"), ("",)],
)
def test_sink_order_must_be_non_empty_unique_names(sink_order: tuple[str, ...]) -> None:
    with pytest.raises(ValidationError):
        AgentConfig.model_validate({"sink_order": list(sink_order)})


def test_unknown_keys_rejected() -> None:
    with pytest.raises(ValidationError):
        AgentConfig.model_validate({"sink_oders": ["sqlite"]})


def test_environment_variables_do_not_affect_config(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "NUTRITION_AGENT_LLM__MODEL",
        "NUTRITION_AGENT_LLM__API_KEY",
        "NUTRITION_AGENT_SQLITE_PATH",
        "NUTRITION_AGENT_DEBUG",
        "OPENAI_API_KEY",
        "DEEPSEEK_API_KEY",
    ):
        monkeypatch.setenv(name, "leaked")

    config = AgentConfig()

    assert config.llm.model == "deepseek-flash"
    assert config.llm.api_key == ""
    assert config.sqlite_path == Path("nutrition.db")
    assert config.debug is False
