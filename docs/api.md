# API

Публичный контракт пакета `nutrition_agent`. Всё, что перечислено ниже,
экспортируется из корневого модуля (`from nutrition_agent import ...`) и
считается стабильным API v1.

## Агент

::: nutrition_agent.agent.NutritionAgent
    options:
      members: [process, commit, write]

## Результаты

::: nutrition_agent.results
    options:
      members: [ProcessResult, ProcessStatus]

## Доменные модели

::: nutrition_agent.domain
    options:
      members: [NutritionEntry, FoodItem, MealType, EntrySource]

## Конфигурация

::: nutrition_agent.config
    options:
      members:
        - AgentConfig
        - LLMConfig
        - STTConfig
        - TimeoutConfig
        - RetryConfig
        - RetryPolicy

## Входы и роутинг

::: nutrition_agent.routing
    options:
      members: [prepare_input, detect_image_mime, PreparedInput]

## Извлечение из LLM

::: nutrition_agent.extraction
    options:
      members: [extract_meal, ExtractedMeal, ExtractionResult, ExtractionStatus]

## LLM-клиент

::: nutrition_agent.llm
    options:
      members:
        - LLMClient
        - OpenAICompatibleLLM
        - Message
        - TextPart
        - ImagePart
        - LLMError

## Sink-и

::: nutrition_agent.sinks.base
    options:
      members: [Sink, SinkResult, ItemWrite]

::: nutrition_agent.sinks.sqlite
    options:
      members: [LocalSQLiteSink]

::: nutrition_agent.sinks.google_health
    options:
      members: [GoogleHealthSink, TokenProvider]

::: nutrition_agent.sinks.fake
    options:
      members: [FakeSink]

## Речь

::: nutrition_agent.speech
    options:
      members: [Transcriber]
