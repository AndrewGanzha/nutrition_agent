"""Configuration models for :class:`AgentConfig`.

Everything the agent needs at runtime lives here and is supplied by the caller.
The package never reads environment variables or config files: defaults are the
only implicit source of values, so a bare ``AgentConfig()`` behaves identically
on every machine.
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from typing import Annotated, Literal, Self

from pydantic import BaseModel, Field, field_validator, model_validator

from nutrition_agent._types import STRICT_MODEL_CONFIG, NonEmptyStr

PositiveSeconds = Annotated[float, Field(gt=0, allow_inf_nan=False)]


class LLMConfig(BaseModel):
    """OpenAI-compatible chat/vision endpoint (DeepSeek by default)."""

    model_config = STRICT_MODEL_CONFIG

    base_url: NonEmptyStr = "https://api.deepseek.com/v1"
    api_key: str = ""
    model: NonEmptyStr = "deepseek-flash"
    vision_model: NonEmptyStr = "deepseek-flash"
    api: Literal["responses", "chat"] = "responses"


class STTConfig(BaseModel):
    """OpenAI-compatible transcription endpoint (used from phase 2 on)."""

    model_config = STRICT_MODEL_CONFIG

    base_url: NonEmptyStr = "https://api.openai.com/v1"
    api_key: str = ""
    model: NonEmptyStr = "whisper-1"
    language: NonEmptyStr | None = None


class RetryPolicy(BaseModel):
    """Attempt budget and exponential backoff for one component."""

    model_config = STRICT_MODEL_CONFIG

    # Deliberately no default. Pydantic does not merge a nested payload with the
    # component-level default, so any default here would turn a partial override
    # such as {"llm": {"backoff_max": 10}} into a silent downgrade of the LLM
    # budget (3 -> default). Requiring it keeps the override loud.
    max_attempts: int = Field(ge=1)
    backoff_base: PositiveSeconds = 1.0
    backoff_max: PositiveSeconds = 3.0

    @model_validator(mode="after")
    def _check_backoff(self) -> Self:
        if self.backoff_max < self.backoff_base:
            raise ValueError("backoff_max must be greater than or equal to backoff_base")
        return self


class RetryConfig(BaseModel):
    """Per-component retry policies."""

    model_config = STRICT_MODEL_CONFIG

    llm: RetryPolicy = Field(default_factory=lambda: RetryPolicy(max_attempts=3))
    stt: RetryPolicy = Field(default_factory=lambda: RetryPolicy(max_attempts=2))
    google: RetryPolicy = Field(default_factory=lambda: RetryPolicy(max_attempts=1))
    sqlite: RetryPolicy = Field(default_factory=lambda: RetryPolicy(max_attempts=1))


class TimeoutConfig(BaseModel):
    """Per-component timeouts, in seconds."""

    model_config = STRICT_MODEL_CONFIG

    llm: PositiveSeconds = 30.0
    stt: PositiveSeconds = 60.0
    google: PositiveSeconds = 10.0
    sqlite: PositiveSeconds = 5.0


def _default_sink_order() -> tuple[str, ...]:
    return ("sqlite", "google_health")


class AgentConfig(BaseModel):
    """Full runtime configuration of :class:`nutrition_agent.NutritionAgent`.

    ``sink_order`` defines which sinks are active and in which order they run:
    sinks whose ``name`` is not listed are not called at all.
    """

    model_config = STRICT_MODEL_CONFIG

    llm: LLMConfig = Field(default_factory=LLMConfig)
    stt: STTConfig = Field(default_factory=STTConfig)
    sqlite_path: Path = Path("nutrition.db")
    sink_order: tuple[NonEmptyStr, ...] = Field(default_factory=_default_sink_order)
    timeouts: TimeoutConfig = Field(default_factory=TimeoutConfig)
    retry: RetryConfig = Field(default_factory=RetryConfig)
    meal_duration: timedelta = timedelta(minutes=30)
    commit_ttl: timedelta = timedelta(minutes=15)
    debug: bool = False

    @field_validator("sink_order")
    @classmethod
    def _check_sink_order(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if not value:
            raise ValueError("sink_order must list at least one sink")
        duplicates = sorted({name for name in value if value.count(name) > 1})
        if duplicates:
            raise ValueError(f"sink_order contains duplicates: {', '.join(duplicates)}")
        return value

    @field_validator("meal_duration", "commit_ttl")
    @classmethod
    def _check_positive_duration(cls, value: timedelta) -> timedelta:
        if value <= timedelta(0):
            raise ValueError("duration must be positive")
        return value
