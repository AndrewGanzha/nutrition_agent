# Google Health: подключение и ограничения

Sink `GoogleHealthSink` пишет каждый `FoodItem` отдельным data point в
`nutrition-log` Google Health API v4 (anonymous food: без ссылки на `Food`,
`serving` не отправляется). Повторный вызов и `commit` после `partial`
дописывают только те items, которых ещё нет в локальном журнале
`sink_writes`, поэтому дублей при ретраях не появляется. Гарантия —
**at-least-once**: падение процесса между успешным POST и записью в журнал
может привести к одному дублю.

## Что нужно в Google Cloud

1. Создайте проект в [Google Cloud Console](https://console.cloud.google.com/)
   и включите **Health API** (Google Health API v4).
2. Настройте **OAuth consent screen**: тип External, режим Testing на время
   разработки.
3. Создайте **OAuth client ID** (Desktop или Web — в зависимости от того, как
   получаете refresh token) и сохраните `client_id`/`client_secret`.
4. Добавьте scope
   `https://www.googleapis.com/auth/googlehealth.nutrition.writeonly`.
5. Добавьте тестовых пользователей (test users) — только они смогут пройти
   авторизацию, пока приложение в Testing.
6. Пройдите OAuth-поток один раз для каждого пользователя и сохраните
   refresh token у себя (в БД, секрет-менеджере, env). **Пакет refresh-токены
   не хранит.**

## Ограничения Testing-режима

- Не больше **100 test users** на проект; остальные получат отказ авторизации.
- **Refresh token живёт 7 дней**, после чего пользователь должен авторизоваться
  заново. Для долгоживущих токенов приложение нужно перевести в Production.
- **Sandbox отсутствует**: тестовые запросы идут в реальный аккаунт test user,
  отдельного «песочного» API нет.
- Access token живёт ~1 час — провайдер должен уметь получать новый по
  refresh token.

## TokenProvider

Sink запрашивает access token через async-callback:

```python
class TokenProvider(Protocol):
    async def __call__(self, user_id: str | None) -> str: ...
```

`user_id` — тот же идентификатор, что передан в `process`/`write`/`commit`
(`None` — анонимный пользователь). Провайдер вызывается один раз на запись и
только если есть незаписанные items. Исключение провайдера превращает эти
items в `failed`, остальные Sink-и продолжают работу.

Минимальная реализация «refresh token → access token» —
[`examples/google_token_provider.py`](../examples/google_token_provider.py):

```python
from examples.google_token_provider import build_google_sink

sink = build_google_sink({None: "<refresh-token>"})
agent = NutritionAgent(config, llm=llm, sinks=[sink])
```

Секреты (`GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, refresh token) остаются
у вызывающей стороны.

## Endpoint и payload

`POST https://health.googleapis.com/v4/users/me/dataTypes/nutrition-log/dataPoints`

Тело — DataPoint v4 с единственным полем `nutritionLog`:

- `interval` — `eaten_at` и `eaten_at + config.meal_duration`, плюс
  `startUtcOffset`/`endUtcOffset` в секундах (`"10800s"`);
- `mealType` — `BREAKFAST|LUNCH|DINNER|SNACK`, для `unknown` поле не
  отправляется;
- `foodDisplayName` — `"<name>, <grams> г"`, либо просто `<name>`;
- `energy.kcal`, `totalCarbohydrate.grams`, `totalFat.grams`,
  `nutrients=[{nutrient: "PROTEIN", quantity: {grams}}]` — только для известных
  значений; нули не подставляются;
- `serving` не отправляется.

## Ответы и ошибки

- 2xx → item `written`; имя data point (если API вернул его в Operation)
  сохраняется в `sink_writes.external_id`.
- 401 (протухший access token), 4xx/5xx, сетевые ошибки → item `failed` с
  текстом `HTTP <code>: <detail>`; остальные items всё равно отправляются.
- Авторетраев нет (`retry.google.max_attempts = 1`): повторить запись можно
  через `agent.write(entry)` или `agent.commit(token)` — уже записанные items
  будут пропущены.
