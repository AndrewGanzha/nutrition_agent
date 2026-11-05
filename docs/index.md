# nutrition-agent

Движок без UI: принимает текст, фото и (во второй фазе) голос с описанием еды,
извлекает структуру через LLM и пишет её в Sink-и — локальный SQLite и Google
Health. Установка, конфигурация и полный пример — в
[README](https://github.com/AndrewGanzha/nutrition_agent#readme).

```python
from nutrition_agent import (
    AgentConfig,
    LLMConfig,
    LocalSQLiteSink,
    NutritionAgent,
    OpenAICompatibleLLM,
)

config = AgentConfig(llm=LLMConfig(api_key="..."))
agent = NutritionAgent(
    config,
    llm=OpenAICompatibleLLM(config.llm, timeout=config.timeouts.llm, retry=config.retry.llm),
    sinks=[LocalSQLiteSink(config.sqlite_path)],
)

result = await agent.process(text="Съел две котлеты и гречку 200г", user_id="alice")
print(result.status, result.entry)
```

## Как это устроено

```text
вход (text / image / audio)
        │
        ▼
prepare_input ── invalid_input
        │
        ▼
extract_meal (LLM + strict schema + один repair) ── ignored / parse_failed
        │
        ▼
NutritionEntry
        │
        ▼
Sink-ы по config.sink_order: SQLite → Google Health
        │
        ▼
ProcessResult (ok / partial / error)
```

- **`process`** — вход → LLM → запись. `dry_run=True` возвращает `commit_token`
  вместо записи.
- **`commit`** — запись entry, сохранённого dry-run токеном (TTL 15 минут).
- **`write`** — запись готового `NutritionEntry` без LLM (backfill).

Идемпотентность — на стороне Sink-ов: таблица `sink_writes` помнит каждый
записанный item, поэтому повторный `write`/`commit` дописывает только
недостающее (at-least-once для Google Health).

## Разделы

- [API](api.md) — публичные классы, протоколы и функции.
- [Use cases](use_cases.md) — сценарии и семантика статусов.
- [Google Health](google_health.md) — OAuth, scope и ограничения Testing.
- [План задач](tasks.md) — принятые решения и бэклог.

## Разработка документации

```bash
uv sync
uv run mkdocs serve      # локальный предпросмотр
uv run mkdocs build      # статическая сборка в site/
```

API-справочник генерируется из docstring-ов через mkdocstrings: при изменении
публичных сигнатур правьте docstring, а не страницу `api.md`.
