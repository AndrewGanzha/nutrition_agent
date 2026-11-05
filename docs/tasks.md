# Tasks: `nutrition_agent`

План реализации пакета по `docs/use_cases.md`. Решения зафиксированы в grilling-сессии; задачи упорядочены по зависимостям.

## Границы v1

Пакет-движок без UI. Один агент, Sink-протокол, два обязательных Sink-а (SQLite + Google Health), DeepSeek для текста и фото, внешний STT для аудио.

**Не входит в v1:** база продуктов (USDA/FatSecret), внутренняя очередь/фоновый демон, локальный STT, Health Connect, exactly-once для Google Health, UI.

## Зафиксированные решения

| Область | Решение |
|---|---|
| Python / пакет | ≥ 3.11, `src/`-layout, дистрибутив `nutrition-agent`, импорт `nutrition_agent` |
| API | `async process`, `commit`, `write`; `ProcessResult`; async Sink-и |
| LLM | `deepseek-flash` через OpenAI-совместимый адаптер; Responses API + `json_schema`, фолбэк на Chat Completions + `json_object`; thinking off |
| STT | OpenAI-совместимый `whisper-1`, только фаза 2 |
| Схема | `NutritionEntry` + `FoodItem[]`; `name` обязателен, остальное Optional |
| Sink-и | последовательно в порядке конфига; ошибка не рвёт цепочку; per-item статусы |
| SQLite | обязателен: аудит, `entries`/`items`/`sink_writes`, replay |
| Google Health | обязателен: anonymous food, один data point на item, at-least-once |
| dry-run | токен в памяти, TTL 15 мин, умирает только при полном успехе |
| Мульти-юзер | `user_id` протянут через `process`/`write`/`Sink.write`/токен |
| Время | `eaten_at` tz-aware, по умолчанию `now(UTC)`; `meal_type` от LLM; `unknown` → без `mealType` |

## Трассировка use cases

| UC | Что покрывает | Задачи |
|---|---|---|
| UC-01 | Текст → валидация → запись | T-010, T-011, T-012, T-013, T-020, T-021, T-022 |
| UC-02 | Аудио → транскрипция → фильтр мусора | T-040, T-041, T-042 |
| UC-03 | Фото → несколько блюд | T-011, T-012, T-013, T-021, T-022 |
| UC-04 | Текст как контекст фото | T-012, T-013 |
| UC-05 | Мульти-Sink, падение одного | T-013, T-020, T-021, T-022, T-014 |
| UC-06 | dry-run и подтверждение | T-015 |

## Допущения

- **Q25b (числа не названы):** таймауты/ретраи — LLM 30 с / 3 попытки, STT 60 с / 2, Google 10 с / 1, SQLite 5 с / 1, backoff 1 → 3 с. Все значения — поля `AgentConfig`; смена чисел не требует правки кода.
- **Дефолты `AgentConfig`, не зафиксированные таблицей решений (T-002):** `llm.base_url="https://api.deepseek.com/v1"`, `llm.model=llm.vision_model="deepseek-flash"`, `llm.api="responses"`, `llm.api_key=""`; `stt.base_url="https://api.openai.com/v1"`, `stt.model="whisper-1"`, `stt.language=None` (автоопределение, T-040; `"ru"` задаётся явно); `sqlite_path="nutrition.db"`; `sink_order=("sqlite", "google_health")` — Sink-и не из списка не вызываются; `meal_duration=30 мин` (интервал Google-точки); `commit_ttl=15 мин`; `debug=False`. Модель конфига `frozen` и `extra="forbid"`: опечатка в имени поля — `ValidationError`. `RetryPolicy.max_attempts` намеренно без дефолта: pydantic не мержит вложенный payload с дефолтом компонента, поэтому частичный оверрайд `retry.llm` без `max_attempts` — `ValidationError`, а не молчаливое падение бюджета попыток 3 → 1.
- **T-003 решения:** `total_*` (grams/kcal/БЖУ) — `None`, если ни один item не знает значение; частичные суммы игнорируют `None` (не подставляют 0). Числовые поля `ge=0`, `confidence` ∈ [0, 1]; `source` обязателен (в отличие от `meal_type=unknown` по умолчанию); `id` — `UUID4` с `default_factory=uuid4`; модели `frozen`, `extra="forbid"`. `notes` принимает любую строку: пустая/пробельная нормализуется в `None` (LLM в strict-схеме часто отдаёт `""`).
- **Контракт Sink (T-020):** `write` возвращает по одному `ItemWrite` на каждый item entry (индексы `0..n-1` ровно по разу); неполный или лишний набор при выводе `sinks_succeeded`/`sinks_failed` считается `failed`, пустой `SinkResult` при непустом entry — тоже. `SinkResult` отвергает пустое имя и дубли индексов, `ProcessResult` — неизвестный статус. T-013 обязан выводить `ok`/`partial`/`error` из item-статусов, а не из пустоты `sinks_succeeded`: sink со смесью `written`+`failed` целиком попадает в `sinks_failed`.
- **T-010 решения:** `OpenAICompatibleLLM(config.llm, timeout=config.timeouts.llm, retry=config.retry.llm, http_client=...)`; SDK-ретраи отключены (`max_retries=0`) — цикл, счётчик попыток и backoff свои, поэтому «ровно `max_attempts` запросов» выполнимо. Модель выбирается по наличию `ImagePart`: `vision_model` иначе `model`. `Message.content` — строка или список `TextPart`/`ImagePart`; картинка уходит как `data:<mime>;base64,...`. В Responses API thinking off = `reasoning={"effort":"none"}`, в Chat — `extra_body={"thinking":{"type":"disabled"}}`; в Chat `response_format=json_object` ставится всегда (даже при `schema=None`), в Responses `text.format` — только когда схема передана. Ошибки — `LLMError`. openai>=3 типизирует `http_client` как `httpx2.AsyncClient`, поэтому шов приёма клиента остаётся `Any` (обычный `httpx`-клиент совместим в рантайме).
- **T-011 решения:** `extract_meal(llm, *, content, eaten_at, source)` — сам собирает `[system, user]`, зовёт `complete` со схемой `meal`, разбирает ответ в `ExtractedMeal`, при ошибке делает ровно один repair (`assistant` с сырым ответом + `user` с текстом ошибки) и только потом `parse_failed`. `ExtractionResult` всегда несёт `raw_response`; политику «отдавать наружу только при `parse_failed`/`debug`» применяет T-013. Схема strict: у каждого объекта `additionalProperties=false` и все поля в `required`, необязательные числа — `["number","null"]`; `is_meal=true` с пустым `items` — ошибка валидации (уходит в repair). Промпт — русский, с внедрённым примером; `RUF001` для этого модуля отключён, т.к. кириллица в прозе — намеренная.
- **T-012 решения:** `prepare_input(*, text, audio_bytes, image_bytes, transcript, eaten_at) -> PreparedInput(status=ok|invalid_input, eaten_at, source, content, error)`; `ok` гарантирует заполненные `eaten_at`/`source`/`content` (`__post_init__`). `transcript` — результат STT, роутинг сам не транскрибирует: `audio_bytes` без `transcript` (и наоборот) → `invalid_input`, причём пустой/пробельный транскрипт считается отсутствующим (пара audio/transcript проверяется по очищенным значениям). Пустая/пробельная строка = отсутствующий вход; `text+image` → части `[TextPart, ImagePart]`, `text+transcript` склеиваются `"\n\n"` (транскрипт первым). `source`: один канал — он же, два и больше — `mixed`. MIME — по магическим байтам (`image/png|jpeg|gif|webp`), лимиты 25/20 МБ проверяются до sniffing. `eaten_at=None` → `now(UTC)`, naive → `invalid_input` (а не `ValidationError`, чтобы T-013 вернул статус).
- **T-013 решения:** `NutritionAgent(config, *, llm, transcriber=None, sinks)`; активные Sink-и = переданные ∩ `sink_order`, в порядке конфига (лишние имена в конфиге пропускаются, дубли имён → `ValueError`). Исключение Sink-а превращается в `SinkResult` со всеми item `failed`, цепочка продолжается. Агрегация: нет `failed` → `ok` (повторная запись, где всё `skipped`, тоже `ok`), есть `failed` и есть хоть один `written`/`skipped` → `partial`, все `failed` → `error`; нет активных Sink-ов → `error`. `raw_response` заполняется только при `parse_failed` или `debug=True`. `dry_run` не вызывает Sink-и, кладёт entry+user_id+TTL в in-memory хранилище и возвращает токен (потребление — T-015). Аудио: лимит проверяется до транскрибера, без транскрибера → `invalid_input`, пустой транскрипт → `ignored`, `LLMError` → `error`.
- **T-014 решения:** `write(entry, user_id=..., dry_run=False)` не зовёт ни LLM, ни транскрайбер: он идёт в общий с `process` путь `_deliver` (dry_run → токен без Sink-ов). Entry передаётся в Sink-и без изменений, поэтому идемпотентность и дозапись — ответственность Sink-ов (`sink_writes`): повторный проход получает `skipped` на уже записанных items, `written` на остальных. Агент не дедуплицирует и не фильтрует items сам; все-`skipped` — `ok` (правило T-013).
- **T-015 решения:** `commit(token)` не зовёт LLM: entry и `user_id` берутся из in-memory токена, TTL — `commit_ttl` (по умолчанию 15 мин). Токен сжигается только при `status="ok"`; `partial`/`error` оставляют его, и повторный `commit` снова шлёт тот же entry — дозапись недостающих items обеспечивают Sink-и через `sink_writes`. Неизвестный/уже использованный токен → `error` «unknown or already committed token», истёкший удаляется и → `error` «commit token expired», Sink-и не вызываются.
- **T-021 решения:** `LocalSQLiteSink(path, *, timeout=5.0)`; соединение открывается на каждый вызов (нет тяжёлого lifecycle, безопасно для нескольких процессов), схема — `CREATE TABLE IF NOT EXISTS` внутри каждого `write` (идемпотентно). Строка `sink_writes` upsert-ится на каждый item; `written`/`skipped` считаются «уже есть», `failed` — нет, поэтому такая строка будет перезаписана при повторе. Дедуп item-ов в `items` — `INSERT OR IGNORE` по `(entry_id, idx)`. `raw_json` = `entry.model_dump_json()` (полный снимок для аудита/replay), но API sink-а его не отдаёт; `entries_on(day, user_id=...)` читает день в UTC и при `user_id=None` выбирает анонимные записи (`user_id IS NULL`), отсутствующий файл БД не создаёт. `external_id = "{entry.id}:{idx}"`, запись атомарна: ошибка БД рвёт вызов и уходит в `failed` на уровне T-013.
- **T-022 решения:** `GoogleHealthSink(sqlite_path, *, token_provider, meal_duration=30 мин, timeout=10 с, sqlite_timeout=5 с, http_client=None)`; URL — константа `https://health.googleapis.com/v4/users/me/dataTypes/nutrition-log/dataPoints`, тело — DataPoint v4: `{"nutritionLog": {...}}`. `interval` — `startTime`/`endTime` в исходном offset `eaten_at` плюс `startUtcOffset`/`endUtcOffset` (обязательные в API, формат `"<seconds>s"`). Пропуски: `mealType` при `unknown`, `energy`/`totalCarbohydrate`/`totalFat`/`nutrients` при `None`, `serving` всегда; `foodDisplayName` = `"{name}, {grams:g} г"` либо `name`. Токен запрашивается один раз на `write` и только если есть незаписанные items; его исключение → все незаписанные items `failed`, HTTP не вызывается. Каждый item — отдельный POST без ретраев: 2xx → `written` (external_id из `response.name` Operation, если есть), истёкший токен/HTTP/сеть → `failed` с текстом `HTTP <code>: <detail>`, остальные items продолжаются. Skip/ledger — общие helper-ы из `sinks/sqlite.py` (`sink_writes.sink="google_health"`), поэтому повтор и backfill постят только недостающие items. DB-ошибка не глотается — её обрабатывает T-013.
- Логирование — stdlib `logging`, без внешних зависимостей.
- `raw_response` отдаётся только при `parse_failed` или `debug=True`; в SQLite `raw_json` пишется всегда (аудит).

## Публичный контракт

```python
class NutritionAgent:
    def __init__(self, config: AgentConfig, *, llm: LLMClient,
                 transcriber: Transcriber | None = None,
                 sinks: list[Sink]) -> None: ...

    async def process(
        self, *, text: str | None = None,
        audio_bytes: bytes | None = None,
        image_bytes: bytes | None = None,
        eaten_at: datetime | None = None,
        dry_run: bool = False,
        user_id: str | None = None,
    ) -> ProcessResult: ...

    async def commit(self, token: str) -> ProcessResult: ...
    async def write(self, entry: NutritionEntry, *,
                    user_id: str | None = None,
                    dry_run: bool = False) -> ProcessResult: ...

@dataclass
class ProcessResult:
    status: Literal["ok", "partial", "ignored", "parse_failed", "invalid_input", "error"]
    entry: NutritionEntry | None
    transcript: str | None
    sink_results: list[SinkResult]
    sinks_succeeded: list[str]      # производное от sink_results
    sinks_failed: list[str]         # производное от sink_results
    commit_token: str | None       # только dry_run
    raw_response: str | None       # только parse_failed / debug
    error: str | None

@dataclass
class ItemWrite:
    index: int
    status: Literal["written", "skipped", "failed"]
    external_id: str | None
    error: str | None

@dataclass
class SinkResult:
    name: str
    items: list[ItemWrite]

class Sink(Protocol):
    name: str
    async def write(self, entry: NutritionEntry, *,
                    user_id: str | None = None) -> SinkResult: ...

class LLMClient(Protocol):
    async def complete(self, messages: list[Message], *,
                       schema: dict | None = None,
                       schema_name: str = "result") -> str: ...

class Transcriber(Protocol):
    async def transcribe(self, audio: bytes, *,
                         filename: str = "audio.ogg") -> str: ...
```

---

# Фаза 1 — текст, фото, запись

## T-001 — Скелет пакета

**Зависит от:** —

**Результат:** `pyproject.toml` (hatchling, `requires-python >=3.11`), `src/nutrition_agent/__init__.py`, зависимости `openai`, `pydantic>=2`, `aiosqlite`, `httpx`; dev-группа `pytest`, `pytest-asyncio`, `mypy`, `ruff` через `[dependency-groups]`; окружение и зависимости — `uv`.

**Acceptance:**
- `uv sync` завершается успешно и создаёт `uv.lock`.
- `uv run python -c "import nutrition_agent"` работает.
- `uv run pytest -q` собирает пустой набор без ошибок; `asyncio_mode = "auto"`.
- `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy` проходят.

## T-002 — `AgentConfig`

**Зависит от:** T-001

**Результат:** Pydantic-модель с полями: `llm` (base_url, api_key, model, vision_model, api=responses|chat), `stt` (base_url, api_key, model, language), `sqlite_path`, `sink_order`, `timeouts` (llm/stt/google/sqlite), `retry` (max_attempts/backoff per component), `meal_duration`, `commit_ttl`, `debug`.

**Acceptance:**
- Дефолты соответствуют таблице решений и разделу «Допущения».
- Невалидные значения (отрицательный timeout, пустой model) → `ValidationError`.
- Пакет не читает env/файлы: тест с `monkeypatch.setenv` не меняет конфиг.

## T-003 — Доменные модели

**Зависит от:** T-001

**Результат:** `MealType` (`breakfast|lunch|dinner|snack|unknown`), `FoodItem` (`name`, `grams`, `kcal`, `protein_g`, `fat_g`, `carbs_g`, `confidence`), `NutritionEntry` (`id: UUID4`, `eaten_at: datetime`, `meal_type`, `items: list[FoodItem]`, `source: text|audio|image|mixed`, `notes`), вычисляемые суммы.

**Acceptance:**
- Naive `eaten_at` → `ValidationError`; tz-aware принимается.
- `items` пустой → `ValidationError`; `name` пустой → `ValidationError`.
- `id` генерируется один раз и уникален между двумя entry.
- Суммы игнорируют `None`, а не считают их нулями.

## T-010 — `LLMClient` и OpenAI-совместимый адаптер

**Зависит от:** T-001, T-002

**Результат:** Protocol из контракта; адаптер `OpenAICompatibleLLM`.
- `api="responses"`: `text.format = {"type": "json_schema", "name": schema_name, "schema": schema, "strict": true}`.
- `api="chat"`: `response_format={"type": "json_object"}`.
- Картинки: `input_image` (Responses) / `image_url` (Chat), data URL.
- Thinking отключён: `reasoning={"effort": "none"}` либо `extra_body={"thinking": {"type": "disabled"}}`.
- Ретраи: 429/5xx/таймаут, `max_attempts`, экспоненциальный backoff.

**Acceptance:**
- Перехваченный HTTP-запрос содержит `json_schema` и `strict` для `api=responses`.
- Для `api=chat` — `json_object` и ни одного `json_schema`.
- 429 → ровно `max_attempts` запросов, затем `LLMError`.
- 400 → без ретраев.
- Изображение уходит как data URL с корректным MIME.

## T-011 — Промпт и схема извлечения

**Зависит от:** T-010, T-003

**Результат:** системный промпт (русский), JSON-пример, схема ответа: `is_meal: bool`, `meal_type`, `items[]` (поля `FoodItem`), `notes`; repair-промпт с текстом ошибки валидации.

**Acceptance:**
- Валидный JSON → `NutritionEntry`.
- `is_meal=false` → `status="ignored"`, Sink-и не вызываются.
- Невалидный JSON → ровно один repair-запрос; успешный repair → `ok`.
- Repair не помог → `parse_failed`, `raw_response` заполнен, Sink-и не вызываются.

## T-012 — Валидация входов и роутинг

**Зависит от:** T-003, T-011

**Результат:** проверка комбинаций и лимитов, сборка сообщений для LLM:
- минимум один вход;
- `text+image` и `text+audio` разрешены;
- `audio+image` → `invalid_input`;
- audio ≤ 25 МБ, image ≤ 20 МБ;
- MIME изображения определяется по магическим байтам (JPEG/PNG/GIF/WebP), `image_mime` не требуется;
- неизвестный формат → `invalid_input`;
- `eaten_at=None` → `now(UTC)`, naive → ошибка.

**Acceptance:**
- Табличный тест по всем комбинациям входов даёт ожидаемый статус.
- PNG/JPEG/WebP/GIF проходят; произвольные байты → `invalid_input`.
- 25 МБ + 1 байт аудио и 20 МБ + 1 байт изображения → `invalid_input`.
- Для text+image в сообщении есть и текст, и картинка.

## T-013 — Оркестрация `process`

**Зависит от:** T-010, T-011, T-012, T-020

**Результат:** полный пайплайн: маршрутизация → LLM → валидация → Sink-и (если не dry-run) → `ProcessResult`.

**Acceptance:**
- Порядок Sink-ов совпадает с `sink_order`.
- Ошибка одного Sink-а не мешает следующему.
- Ни один Sink не записал → `status="error"`; часть записала → `partial`; все записали → `ok`.
- `dry_run=True` → `sink.write` не вызывался ни разу, `commit_token` заполнен.
- `raw_response` пуст при `ok` и `debug=False`.

## T-014 — `write(entry)` и backfill

**Зависит от:** T-013

**Результат:** публичный `write(entry, user_id=..., dry_run=False)` без LLM; идемпотентный проход по Sink-ам.

**Acceptance:**
- LLM-клиент не вызывается.
- Повторный `write` того же entry не создаёт дублей: SQLite — `skipped`, Google — `skipped` по `sink_writes`.
- Частично записанный entry дозаписывает только отсутствующие items.

## T-015 — `dry_run` / `commit`

**Зависит от:** T-013

**Результат:** in-memory store токенов (UUID → entry + user_id, TTL 15 мин), `commit(token)`.

**Acceptance:**
- `commit` не вызывает LLM и пишет ровно сохранённый entry.
- Частичный успех не сжигает токен; повторный `commit` пишет только незаписанные items.
- Полный успех сжигает токен; повторный `commit` → `error`.
- Истёкший TTL → `error`.
- `user_id` из токена попадает в Sink-и.

## T-020 — Sink-протокол и `SinkResult`

**Зависит от:** T-001, T-003

**Результат:** Protocol, dataclasses из контракта, `FakeSink` для тестов.

**Acceptance:**
- `SinkResult` валидирует статусы `written|skipped|failed`.
- `FakeSink` умеет задавать результат по item и фиксирует вызовы.
- `sinks_succeeded`/`sinks_failed` в `ProcessResult` вычисляются из `sink_results`.

## T-021 — `LocalSQLiteSink`

**Зависит от:** T-020

**Результат:** `aiosqlite`, таблицы:
```sql
entries(id TEXT PRIMARY KEY, user_id TEXT, eaten_at TEXT, meal_type TEXT,
        source TEXT, raw_json TEXT, created_at TEXT)
items(entry_id TEXT, idx INTEGER, name TEXT, grams REAL, kcal REAL,
      protein_g REAL, fat_g REAL, carbs_g REAL, confidence REAL,
      PRIMARY KEY (entry_id, idx))
sink_writes(entry_id TEXT, sink TEXT, item_idx INTEGER, external_id TEXT,
            status TEXT, written_at TEXT,
            PRIMARY KEY (entry_id, sink, item_idx))
```
Инициализация схемы идемпотентна.

**Acceptance:**
- Повторная запись того же entry → одна строка на item, статус `skipped`.
- Запрос по дню и `user_id` возвращает ожидаемые entry.
- `raw_json` сохранён, но наружу через API не отдаётся.
- `sink_writes` содержит запись для каждого item.

## T-022 — `GoogleHealthSink`

**Зависит от:** T-020

**Результат:** POST `https://health.googleapis.com/v4/users/me/dataTypes/nutrition-log/dataPoints` по одному на item; токен через async-callback `TokenProvider(user_id)`; пропуск items, найденных в `sink_writes`; без авторетраев.

Маппинг:
- `interval = [eaten_at, eaten_at + meal_duration]`
- `mealType` — `BREAKFAST|LUNCH|DINNER|SNACK`, `unknown` → поле не отправляется
- `foodDisplayName = f"{name}, {grams} г"` (если grams есть, иначе `name`)
- `energy.kcal` ← `kcal`, `totalCarbohydrate` ← `carbs_g`, `totalFat` ← `fat_g`
- `nutrients = [{nutrient: "PROTEIN", quantity: {grams: protein_g}}]`
- отсутствующие значения не отправляются, нули не подставляются
- `serving` не отправляется

**Acceptance:**
- Snapshot payload: все поля на месте, `None` отсутствуют.
- `meal_type=unknown` → в payload нет `mealType`.
- 401 → item `failed` с ошибкой, исключение не выходит наружу.
- Один item упал, два записались → `SinkResult` с двумя `written` и одним `failed`.
- Повторный вызов пропускает уже записанные items.
- `TokenProvider` вызывается с переданным `user_id`.

## T-023 — Google OAuth: документация и провайдер токена

**Зависит от:** T-022

**Результат:** `TokenProvider` Protocol, пример реализации (refresh token → access token), `docs/google_health.md`: scope `googlehealth.nutrition.writeonly`, test users, лимит 100 пользователей, 7-дневные refresh-токены в Testing, отсутствие sandbox.

**Acceptance:**
- Пример кода получает access token по refresh token и передаёт его в Sink.
- В документе перечислены шаги Google Cloud setup и ограничения Testing-режима.
- Пакет не хранит refresh-токены.

## T-024 — Логирование

**Зависит от:** T-013

**Результат:** `logging` с полями `entry_id`, `sink`, `user_id`, `item_idx`, `duration_ms`; без секретов и без `raw_json` по умолчанию.

**Acceptance:**
- На `partial` есть warning на каждый failed item.
- В логах нет API-ключей, access/refresh токенов и `raw_json`.

## T-030 — Офлайн-тесты

**Зависит от:** T-013, T-014, T-015, T-021, T-022, T-024

**Результат:** unit + integration на фейках, без сети.

**Acceptance:**
- `pytest -q` зелёный при заблокированных сокетах.
- Покрыты: все статусы `ProcessResult`, dry-run/commit, partial write, повторная запись, маппинг Google, SQLite-запросы.
- Нет тестов на копирование/форвардинг и на текст исходников.

## T-031 — Smoke

**Зависит от:** T-030

**Результат:** `scripts/smoke.py` (вне CI, читает ключи из env).

**Acceptance:**
- Текст «Съел две котлеты и гречку 200г» → entry с ≥ 2 items.
- Фото блюда → entry с ≥ 1 item.
- `dry_run` → `commit` пишет один раз; повторный `commit` → `error`.
- При настроенном test user запись видна в Google Health.

## T-032 — Документация

**Зависит от:** T-030

**Результат:** `docs/use_cases.md` приведён к решениям (Google Fit → Google Health, `partial`, `commit`/`write`, `user_id`); `README.md` с установкой, конфигом и примером.

**Acceptance:**
- В `use_cases.md` нет упоминаний Google Fit как рабочей интеграции.
- README-пример компилируется и соответствует реальным сигнатурам.

---

# Фаза 2 — аудио

## T-040 — `Transcriber`

**Зависит от:** T-002, T-013

**Результат:** OpenAI-совместимый адаптер (`whisper-1`), `language=None`, таймаут 60 с, 2 попытки.

**Acceptance:**
- Фейковый ответ «...» → текст возвращается как есть.
- Пустая строка → пустая строка, без исключения.
- 429 → ровно 2 запроса.
- Аудио > 25 МБ отсекается до сетевого вызова.

## T-041 — Аудио-роутинг

**Зависит от:** T-040

**Результат:** `audio_bytes` → `transcribe` → тот же extraction-пайплайн; `text+audio` → транскрипт + текст.

**Acceptance:**
- Пустой транскрипт → `status="ignored"`, Sink-и не вызываются.
- Транскрипт-мусор → `is_meal=false` → `ignored`.
- Валидный транскрипт → entry, `source="audio"` или `mixed` для text+audio.

## T-042 — Smoke аудио

**Зависит от:** T-041

**Результат:** ручной прогон на реальном `.ogg`.

**Acceptance:**
- Голосовое с едой → entry с items.
- «Спасибо» → `ignored`.

---

# Definition of Done (v1)

- `pytest -q` зелёный офлайн.
- Text- и image-smoke дают валидный entry.
- SQLite содержит entry + items + `sink_writes`.
- Google Health пишет nutrition-log для test user; повторный `write` не дублирует.
- `dry_run` → `commit` пишет ровно один раз; partial commit дозаписывает остаток.
- `docs/use_cases.md` и `README.md` соответствуют коду.
