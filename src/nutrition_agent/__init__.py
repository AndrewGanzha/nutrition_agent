"""Nutrition agent: turn free-form meal input into structured nutrition entries."""

from nutrition_agent.agent import NutritionAgent
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
from nutrition_agent.routing import (
    InputStatus,
    PreparedInput,
    detect_image_mime,
    prepare_input,
)
from nutrition_agent.sinks import (
    FakeSink,
    ItemStatus,
    ItemWrite,
    Sink,
    SinkResult,
)
from nutrition_agent.speech import Transcriber

__all__ = [
    "AgentConfig",
    "EntrySource",
    "ExtractedMeal",
    "ExtractionResult",
    "ExtractionStatus",
    "FakeSink",
    "FoodItem",
    "ImagePart",
    "InputStatus",
    "ItemStatus",
    "ItemWrite",
    "LLMClient",
    "LLMConfig",
    "LLMError",
    "MealType",
    "Message",
    "NutritionAgent",
    "NutritionEntry",
    "OpenAICompatibleLLM",
    "PreparedInput",
    "ProcessResult",
    "ProcessStatus",
    "RetryConfig",
    "RetryPolicy",
    "STTConfig",
    "Sink",
    "SinkResult",
    "TextPart",
    "TimeoutConfig",
    "Transcriber",
    "detect_image_mime",
    "extract_meal",
    "prepare_input",
]
