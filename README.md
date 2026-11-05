# nutrition-agent

Движок без UI: превращает текст, фото и голос с описанием еды в структурированные
приёмы пищи и пишет их в Sink-и (SQLite + Google Health). LLM — DeepSeek через
OpenAI-совместимый API, STT — внешний `whisper-1` (фаза 2).

- Python ≥ 3.11, `src/`-layout, импорт `nutrition_agent`.
- Пакет не читает env и файлы конфигурации: всё приходит через `AgentConfig`.
- Запись идемпотентна на уровне Sink-ов (`sink_writes`): повторный `write`/`commit`
  дописывает только недостающие items.

## Установка

```bash
uv sync                 # создаёт .venv и ставит зависимости из uv.lock
uv run python -c "import nutrition_agent"
```

Без `uv`: `pip install -e .`.

## Быстрый старт

```python
import asyncio
import os

from nutrition_agent import (
    AgentConfig,
    GoogleHealthSink,
    LLMConfig,
    LocalSQLiteSink,
    NutritionAgent,
    OpenAICompatibleLLM,
    Sink,
    TokenProvider,
)


class EnvTokenProvider:
    """Return an OAuth access token for the user (see docs/google_health.md)."""

    async def __call__(self, user_id: str | None) -> str:
        return os.environ["GOOGLE_ACCESS_TOKEN"]


async def main() -> None:
    config = AgentConfig(llm=LLMConfig(api_key=os.environ["DEEPSEEK_API_KEY"]))
    llm = OpenAICompatibleLLM(config.llm, timeout=config.timeouts.llm, retry=config.retry.llm)
    token_provider: TokenProvider = EnvTokenProvider()
    sinks: list[Sink] = [
        LocalSQLiteSink(config.sqlite_path, timeout=config.timeouts.sqlite),
        GoogleHealthSink(
            config.sqlite_path,
            token_provider=token_provider,
            meal_duration=config.meal_duration,
            timeout=config.timeouts.google,
            sqlite_timeout=config.timeouts.sqlite,
        ),
    ]
    agent = NutritionAgent(config, llm=llm, sinks=sinks)

    preview = await agent.process(text="Съел две котлеты и гречку 200г", dry_run=True)
    print(preview.status, preview.entry)

    if preview.commit_token is not None:
        result = await agent.commit(preview.commit_token)
        print(result.status, result.sinks_succeeded, result.sinks_failed)


asyncio.run(main())
```

## Приём пищи в два шага (dry-run → commit)

`process(..., dry_run=True)` выполняет весь анализ, но не пишет в Sink-и и
возвращает `commit_token` (TTL 15 минут, in-memory). `commit(token)` пишет ровно
сохранённый entry с сохранённым `user_id`.

- `partial`/`error` токен не сжигают — повторный `commit` допишет остаток.
- `ok` сжигает токен; повторный `commit` → `status="error"`.
- Истёкший токен → `error` без вызовов Sink-ов.

## Backfill без LLM

```python
if preview.entry is not None:
    backfill = await agent.write(preview.entry, user_id="alice")
    print(backfill.status, backfill.sinks_succeeded, backfill.sinks_failed)
```

`write(entry, user_id=..., dry_run=False)` не вызывает LLM: Sink-и сами решают по
`sink_writes`, что уже записано (`skipped`), а что нужно дописать (`written`).

## Конфигурация

`AgentConfig` — pydantic-модель (`frozen`, `extra="forbid"`): опечатка в имени поля
даёт `ValidationError`. Дефолты:

| Поле | Значение |
| --- | --- |
| `llm.base_url` / `llm.api_key` | `https://api.deepseek.com/v1` / `""` |
| `llm.model` / `llm.vision_model` | `deepseek-flash` / `deepseek-flash` |
| `llm.api` | `responses` (фолбэк-режим `chat`) |
| `stt.*` | OpenAI-совместимый `whisper-1`, `language=None` |
| `sqlite_path` | `nutrition.db` |
| `sink_order` | `("sqlite", "google_health")` — Sink-и не из списка не вызываются |
| `timeouts` | LLM 30 с, STT 60 с, Google 10 с, SQLite 5 с |
| `retry` | LLM 3 попытки, STT 2, Google/SQLite 1; backoff 1→3 с |
| `meal_duration` | 30 мин (интервал data point в Google Health) |
| `commit_ttl` | 15 мин |
| `debug` | `False` (при `True` в `ProcessResult.raw_response` попадает сырой ответ LLM) |

Частичный оверрайд `retry.<component>` требует явный `max_attempts` — pydantic не
мержит вложенный payload с дефолтом компонента.

## Sink-и

| Sink | Что делает |
| --- | --- |
| `LocalSQLiteSink(path, *, timeout=5.0)` | `entries`/`items`/`sink_writes`, `raw_json` для аудита; `entries_on(day, user_id=...)` — выборка за UTC-день |
| `GoogleHealthSink(sqlite_path, *, token_provider, ...)` | один POST на item в `nutrition-log`, anonymous food; пропускает items из `sink_writes`; без авторетраев |
| `FakeSink` | тестовый Sink с настраиваемыми статусами по item |

`Sink` — протокол: `name` + `async write(entry, *, user_id=None) -> SinkResult`.
Своя реализация подключается без изменения агента.

Подключение Google (scope, тестовые пользователи, лимиты Testing) —
[`docs/google_health.md`](docs/google_health.md). Пример провайдера токена —
[`examples/google_token_provider.py`](examples/google_token_provider.py).

## Статусы `ProcessResult`

| Статус | Когда |
| --- | --- |
| `ok` | все items всех активных Sink-ов `written`/`skipped` |
| `partial` | часть items/Sink-ов упала, но что-то записано |
| `ignored` | LLM сказал `is_meal=false` или пустой транскрипт |
| `parse_failed` | невалидный JSON даже после одного repair |
| `invalid_input` | пустой/несовместимый вход, лимит или неизвестный формат |
| `error` | LLM/сеть упали или ни один Sink не записал entry |

## Разработка

```bash
uv run pytest -q        # офлайн: сокеты заблокированы в conftest.py
uv run ruff check .
uv run ruff format --check .
uv run mypy             # strict: src, tests, scripts, examples
```

Ручной прогон против реальных API (нужны ключи, вне CI):

```bash
DEEPSEEK_API_KEY=... SMOKE_IMAGE=meal.jpg \
GOOGLE_CLIENT_ID=... GOOGLE_CLIENT_SECRET=... GOOGLE_REFRESH_TOKEN=... \
uv run python scripts/smoke.py
```

Сценарии использования и семантика статусов — [`docs/use_cases.md`](docs/use_cases.md),
план задач и принятые решения — [`docs/tasks.md`](docs/tasks.md).
