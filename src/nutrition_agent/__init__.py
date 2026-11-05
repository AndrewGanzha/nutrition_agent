"""Nutrition agent: turn free-form meal input into structured nutrition entries."""

from nutrition_agent.config import (
    AgentConfig,
    LLMConfig,
    RetryConfig,
    RetryPolicy,
    STTConfig,
    TimeoutConfig,
)
from nutrition_agent.domain import EntrySource, FoodItem, MealType, NutritionEntry
from nutrition_agent.extraction import (
    ExtractedMeal,
    ExtractionResult,
    ExtractionStatus,
    extract_meal,
)
from nutrition_agent.llm import (
    ImagePart,
    LLMClient,
    LLMError,
    Message,
    OpenAICompatibleLLM,
    TextPart,
)
from nutrition_agent.results import ProcessResult, ProcessStatus
from nutrition_agent.sinks import (
    FakeSink,
    ItemStatus,
    ItemWrite,
    Sink,
    SinkResult,
)

__all__ = [
    "AgentConfig",
    "EntrySource",
    "ExtractedMeal",
    "ExtractionResult",
    "ExtractionStatus",
    "FakeSink",
    "FoodItem",
    "ImagePart",
    "ItemStatus",
    "ItemWrite",
    "LLMClient",
    "LLMConfig",
    "LLMError",
    "MealType",
    "Message",
    "NutritionEntry",
    "OpenAICompatibleLLM",
    "ProcessResult",
    "ProcessStatus",
    "RetryConfig",
    "RetryPolicy",
    "STTConfig",
    "Sink",
    "SinkResult",
    "TextPart",
    "TimeoutConfig",
    "extract_meal",
]
