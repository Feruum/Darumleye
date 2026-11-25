# Spec Review: наблюдение и расследование инцидентов

Langfuse позволяет сравнить парный (`multi_agent`, по умолчанию) и прямой (`multi_agent_direct`) маршруты на GPT-6 Luna по метаданным прогонов. Исторические запуски сохраняют метку `legacy`. Он **не** вычисляет вердикт и не является источником содержания отчёта. OpenTelemetry создаёт один trace на попытку прогона, а `sessionId` совпадает с UUID прогона (`run_id`) в паспорте и durable-очереди. Повтор той же задачи после сбоя имеет новый trace и тот же session ID. CLI без регламентов автоматически сохраняет UUID для того же входа, режима и модели в `--checkpoint-dir`; при работе с регламентами передайте один и тот же `--run-id UUID` при возобновлении неизменного прогона. Новый повтор архивного документа сохраняет парный или прямой маршрут. Повтор отчёта `legacy` либо отчёта без режима использует `multi_agent_direct`. Восстановленная старая задача очереди с тем же UUID продолжает исходный пайплайн. Для нового независимого эксперимента используйте новый каталог контрольных точек.

В трассе `multi_agent_direct` после экспертных веток следует общий валидатор. Три стадии парных валидаторов появляются только в `multi_agent`. Чтобы оценить задержку целого прогона, сравнивайте длительности корневых стадий для одинакового документа и учитывайте попытки, завершившиеся ошибкой.

## Локальный запуск

1. Скопируйте `vibe/.env.example` в `vibe/.env`, если файла ещё нет. Для профиля Langfuse задайте собственные `LANGFUSE_POSTGRES_PASSWORD`, `LANGFUSE_CLICKHOUSE_PASSWORD`, `LANGFUSE_REDIS_PASSWORD`, `LANGFUSE_MINIO_PASSWORD`, `LANGFUSE_SALT`, `LANGFUSE_NEXTAUTH_SECRET` (независимые случайные значения) и `LANGFUSE_ENCRYPTION_KEY` (64 шестнадцатеричных символа). Для пароля PostgreSQL используйте только символы, допустимые в URL без экранирования. Локальные демонстрационные значения в Compose не являются production-секретами.
2. Из `vibe/` запустите `docker compose --profile langfuse up -d langfuse-web langfuse-worker langfuse-alerts`. По умолчанию обе версии сервера закреплены на 4.86.0; используется совместимый [официальный Compose](https://github.com/langfuse/langfuse/blob/v4.86.0/docker-compose.yml). В профиле используются отдельные PostgreSQL, ClickHouse, Redis и S3-совместимое хранилище. Они не публикуют порты; UI доступен только через loopback `http://localhost:3333` (можно изменить `LANGFUSE_PORT`). Контейнер `langfuse-alerts` до создания проектных ключей пишет `DISABLED` и не обращается к API. Обычный запуск приложения не запускает профиль.
3. В UI Langfuse создайте локальный проект Spec Review и его API-ключи. В `vibe/.env` добавьте `AGENT_LANGFUSE_BASE_URL=http://langfuse-web:3000`, `AGENT_LANGFUSE_PUBLIC_KEY` и `AGENT_LANGFUSE_SECRET_KEY`. Для standalone CLI используйте эти же имена в корневом `.env`, но URL `http://localhost:3333`.
4. Пересоздайте контейнер agent (`docker compose up -d --build agent`) и обновите конфигурацию монитора (`docker compose --profile langfuse up -d langfuse-alerts`), затем выполните обычное ревью. В CLI можно использовать `agent-spec-review … --review-mode multi_agent`. Наблюдения появляются в Langfuse после завершения этапов. Для другого уже работающего совместимого Langfuse (сервер ≥4.59.0) укажите его базовый URL и соответствующие проектные ключи. Для внешнего сервера используйте HTTPS.
5. Создайте дашборды скриптом ниже. Начальные алерты уже проверяются локальным `langfuse-alerts` каждые пять минут; при необходимости настройте нативные уведомления Langfuse по таблице. Профиль не привязан к существующему проекту Langfuse и не создаёт учётную запись автоматически.

Все три параметра `AGENT_LANGFUSE_*` должны присутствовать; незаполненная конфигурация отключает экспорт. `OTEL_SDK_DISABLED=true` отключает OTel целиком. Существующий `AGENT_OTLP_ENDPOINT` независимо сохраняет экспорт инфраструктурных метрик и трассировок. Официальный Langfuse Python SDK v5 (lock: 5.2.0; [совместимость с сервером](https://langfuse.com/self-hosting/upgrade/versioning)) подключается к тому же OpenTelemetry `TracerProvider` через `tracer_provider=`. SDK получает явный `should_export_span` и специализированный `span_exporter`, который использует документированный [OTLP HTTP/protobuf endpoint](https://langfuse.com/integrations/native/opentelemetry), `x-langfuse-ingestion-version: 4` и фоновую пакетную отправку SDK. Автоматический захват LLM-промптов и ответов не включён. Экспорт — best effort, недоступный Langfuse не блокирует анализ и не меняет вердикт.

## Какие данные экспортируются

| Поле | Значение |
| --- | --- |
| Session ID / metadata `run_id` | UUID работы, не пользовательский идентификатор |
| Trace name | `spec-review` |
| Metadata `review_mode`, `source` | Новые запуски: `multi_agent` / `multi_agent_direct`. Исторические: `legacy`. Источник: `cli` / `api` / `worker`. |
| Name | Статическое имя стадии / эксперта / парного валидатора |
| Model / usage | Техническое имя модели и токены, сообщённые провайдером |
| Metadata `reasoning_effort` | Только настроенное `low` / `medium`, без рассуждений модели |
| Release / version / `prompt_version` | Версия сервиса, git revision, SHA-256 инструкций |
| Outcome / level / `error_type` / `http_status` | Безопасное состояние и код, без сообщения исключения |
| `cache_hit`, `validation_errors`, `correction_attempts` | Использование сохранённого результата и ошибки повторных проверок |
| `criteria_covered`, `criteria_expected`, `set_criteria_covered`, `set_criteria_expected`, `checklist_covered`, `checklist_expected`, `coverage_complete` | Покрытие уникальных обязательных критериев; незаполненный пункт-заглушка не считается проверенным; не semantic accuracy |
| `findings`, `validated_citations`, `observations`, `nodes`, `edges`, `unresolved_count` | Счётчики результата и графа |
| `drex_supported`, `drex_uncertain`, `drex_unsupported` | Независимая оценка (не гарантированная истина) |

Завершённые trace также публикуют типизированные `spec_review_*` Scores через [официальный Scores API](https://langfuse.com/docs/evaluation/evaluation-methods/scores-via-sdk): булевы `terminal_success`, `coverage_complete`, числовые счётчики критериев, замечаний, цитат, Drex и нерешённых разногласий, категориальный `verdict`. Идентификаторы score стабильны для одной попытки прогона. Повторная доставка не создаёт вторую запись с тем же временем. Отменённая или незафиксированная работа не получает финальных quality scores.

Не экспортируются тексты файлов, имена файлов, имя владельца, содержание инструкций и ответов, цитаты, параметры HTTP-запросов, стек исключения и секреты. Фильтр SDK отбрасывает все чужие instrumentation scope и любые span с атрибутами вне списка разрешённых **до** SDK media processing. Экспортёр повторно проверяет значения, очищает события, ссылки, описание статуса и ресурс OTel. В проекте хранятся только агрегаты и технические ID; читать доказательства нужно из авторизованного отчёта. Самостоятельное развёртывание не отменяет контроль доступа к проекту и правила хранения журналов.

## Автоматическая настройка дашбордов

Из корня репозитория, с установленными зависимостями Python-агента:

```bash
# Только просмотр локальной декларации: сеть и ключи не нужны.
vibe/services/agent/.venv/bin/python vibe/services/agent/scripts/provision_langfuse_dashboards.py

# Применить к уже созданному проекту; значения AGENT_LANGFUSE_* берутся из .env.
# Если ключи настроены в vibe/.env, используйте --env-file vibe/.env и
# переопределите контейнерный URL на http://localhost:3333 для запуска с хоста.
vibe/services/agent/.venv/bin/python vibe/services/agent/scripts/provision_langfuse_dashboards.py --env-file .env --apply
```

Скрипт создаёт пять дашбордов с названиями `Spec Review — …`, виджеты и недостающие размещения. Повторный запуск использует существующие объекты с тем же именем, сохраняет пользовательские изменения и ничего не удаляет. Если одно имя соответствует нескольким объектам, настройка останавливается: сначала устраните неоднозначность вручную. При частичном сбое после исправления причины можно повторить команду. Применение явно включается флагом `--apply`; запросы уходят только в настроенный Langfuse, секреты не выводятся.

Используются [публичные нестабильные API Langfuse](https://langfuse.com/changelog/2026-07-17-dashboard-and-widget-operations-in-api), проверенные по исходным [схемам виджетов](https://github.com/langfuse/langfuse/blob/main/web/src/features/public-api/types/unstable-dashboard-widgets.ts), [дашбордов](https://github.com/langfuse/langfuse/blob/main/web/src/features/public-api/types/unstable-dashboards.ts) и [query data model](https://github.com/langfuse/langfuse/blob/main/packages/shared/src/features/query/dataModel.ts). Требуется актуальный совместимый Langfuse v4, где доступен `/api/public/unstable/dashboard-widgets`. При `404`/несовместимом API воспользуйтесь ручной настройкой ниже. Скрипт не изменяет домашний дашборд и не настраивает внешние уведомления.

### Сравнение моделей и версий промптов

По умолчанию **Model Evaluation** содержит отдельные графики количества model calls, p95 задержки генераций и суммы сообщённых токенов по `providedModelName` для активных маршрутов `multi_agent` и `multi_agent_direct`. Метку `legacy` используйте для просмотра прежних трасс. Для сравнения версий промптов используется фактически наблюдаемое поле Metadata `prompt_version`: SHA-256 точной строки системных инструкций после выбора этапа и варианта (включая схему/коррекцию). Это не версия промпта из Langfuse Prompt Management и не хеш исходного банковского документа.

Как найти наблюдаемую версию без чтения банковского текста: в Langfuse откройте **Observations**, отфильтруйте `Trace name = spec-review`, `Type = GENERATION`, при необходимости имя стадии (например, `extraction`) и модель. На карточке наблюдения откройте Metadata и скопируйте ровно 64 шестнадцатеричных символа из `prompt_version`. В штатном экспорте полных промптов и текста ТЗ нет. Для корректного сравнения выбирайте версии одной сопоставимой стадии: отдельная роль или корректирующая попытка может иметь другой хеш.

Добавление графиков для выбранных хешей:

```bash
# Пример с двумя синтетическими хешами; замените их на наблюдаемые.
vibe/services/agent/.venv/bin/python vibe/services/agent/scripts/provision_langfuse_dashboards.py \
  --env-file .env \
  --prompt-version fa8b8b09dec8f55210d33bd44473bbd77d85a1cf7acf8e718b5ac5e8dc478d05 \
  --prompt-version 91084c00beea5999a3bd8ebdcbe6644bd457fcc828149e759915223ae26a2bf3 \
  --apply
```

У Metrics v2 нет группировки по произвольному ключу Metadata. Поэтому для каждого явно выбранного хеша добавляются шесть виджетов: количество генераций, p95 и токены по модели в двух режимах, с точным поддерживаемым фильтром `stringObject(metadata.prompt_version = SHA256)`. Неподдерживаемый dimension не используется. В новом проекте базовый набор содержит 39 виджетов. Каждый выбранный хеш добавляет шесть виджетов. При обновлении ранее настроенного проекта виджеты старого режима legacy остаются доступными под прежними именами. Сравнение качества и полноты по эталонным дефектам остаётся в A/B benchmark, а не выводится из количества генераций.

## Шаблон ручной настройки дашбордов

В Langfuse **Dashboards → Widgets → New Widget** создайте виджеты в `Observations` и добавьте их в пять дашбордов. По умолчанию фильтр `Trace name = spec-review`, окно 7 дней. Группируйте по `name`, `model`, `level`, `release` или фильтруйте по отдельным ключам Metadata (указаны в таблице).

| Дашборд | Виджеты и настройки |
| --- | --- |
| Review Operations | Count операций с `name=spec-review` по уровню; Count `outcome=completed`/`failed`; p95 Latency `name=review-analysis`; Count `outcome=not_committed`; Count операций с `cache_hit=true`; возобновлённые worker attempts: Count `name=checkpoint`, Metadata `source=worker`, `outcome=completed`. |
| Agent Performance | P50/P95 Latency и Count по `name` для `logic-inspector`, `scenario-builder`, `gap-hunter`, `contract-inspector`, `risk-inspector`, `reference-rag`, `pair-logic-scenario`, `pair-gap-contract`, `pair-risk-reference`; input/output tokens по имени вызова `multi_*` и модели; Count ошибок стадий по Name. |
| Evidence Quality | Среднее Boolean Score `spec_review_coverage_complete` (доля полностью покрытых отчётов); Count стадий `validation_errors` и `correction_attempts` (значение >0); Numeric Scores `spec_review_unresolved_count`, `spec_review_findings`, `spec_review_validated_citations`, `spec_review_drex_*` по релизам. |
| Cost Intelligence | Суммы Input Tokens и Output Tokens, Cost по модели/имени стадии, фильтр Type=GENERATION; для парного и прямого маршрутов используйте metadata `review_mode`. Не интерпретируйте стоимость Langfuse как счёт провайдера при неизвестном тарифе; отсутствующая usage не равна нулю. Локальный файл `--metrics-output` учитывает вызовы на уровне приложения, а не скрытые ретраи SDK или отдельные счета Drex. |
| Model Evaluation | Count и P95 Latency по release и `review_mode`; count/p95/tokens генераций по `providedModelName` отдельно по режимам и по выбранным точным хешам `prompt_version`; Boolean Score `spec_review_terminal_success`; сравнение полноты и распределений Categorical Score `spec_review_verdict`. Итог A/B и ручная разметка находятся в результатах benchmark, не выводятся из числа замечаний. |

Маркер `checkpoint` в Review Operations означает один повторный вход в обработку durable-задания после того, как предшествующая попытка израсходовала часть сохранённого в PostgreSQL лимита активного времени. При первом входе этот лимит равен первоначальному; при graceful requeue или восстановлении просроченной lease предыдущий расход учитывается. Счётчик измеряет возобновлённые **попытки**, не число уникальных задач и не число повторно использованных LLM-ответов. Переиспользование каждого результата независимо отражается `cache_hit=true`; это отдельный виджет. Как и любая best-effort телеметрия, счётчик не восстанавливает события, которые не были переданы при внезапном прекращении процесса или недоступности Langfuse.

### Начальные пороги алертов

В **Observability → Alerts → New Alert** (Langfuse v4) используйте соответствующий источник данных, фильтр общего имени trace и дополнительные условия:

| Алерт | Условия | Порог | Окно |
| --- | --- | --- | --- |
| Review failed | Name `spec-review`, Level ERROR | Count >= 1 | 1 час |
| Required coverage incomplete | Scores (Boolean) `spec_review_coverage_complete=false` | Count >= 1 | 1 час |
| Review p95 latency | Name `review-analysis` | P95 Latency > 720 000 мс | 1 час |

Выберите `Notify NO DATA` или `Silence NO DATA` для покрытия и p95, чтобы отсутствие телеметрии не считалось успешным анализом. Каналы оповещения привязывает администратор проекта по своему выбору, без передачи содержимого документа. Официальные инструкции: [дашборды](https://langfuse.com/docs/metrics/features/custom-dashboards), [алерты](https://langfuse.com/docs/observability/features/alerts). В публичном API Langfuse нет операции создания нативных alert monitors, поэтому их нельзя автоматически provision без использования непубличного UI API.

Если нужна доставка внутри собственной инфраструктуры без учётной записи Slack/PagerDuty/GitHub, официальный канал Langfuse **Alert automation → Webhook** может вызывать внутренний HTTP(S) endpoint мониторинга. Администратор проекта задаёт адрес собственного сервиса-получателя и настраивает проверку HMAC-подписи по инструкции Langfuse; затем связывает automation с нативным алертом. Для базовой конфигурации достаточно локальных JSON-журналов `langfuse-alerts`, webhook-получатель и внешние каналы не разворачиваются и не вызываются автоматически.

### Проверка тех же правил через Metrics API

Для мониторинга сервисом или планировщиком, без настройки нативных каналов Langfuse, предусмотрен независимый read-only скрипт:

```bash
# Локальное описание трёх правил, без сети.
vibe/services/agent/.venv/bin/python vibe/services/agent/scripts/check_langfuse_alerts.py

# Одна реальная проверка агрегированных метрик настроенного проекта.
vibe/services/agent/.venv/bin/python vibe/services/agent/scripts/check_langfuse_alerts.py --env-file .env --check
```

Скрипт использует официальный [Metrics API v2](https://langfuse.com/docs/metrics/features/metrics-api), не выгружает тексты, трассы или отчёты, проверяет скользящее окно в один час и выводит числовые значения и состояния каждого правила. Код выхода: `0` — все в норме, `1` — минимум один порог нарушен, `2` — нет достаточных данных либо API недоступен. Для покрытия число неполных результатов получается как количество Boolean Scores минус сумма их значений (0/1). Отсутствие наблюдений p95 или оценок покрытия обозначается `NO_DATA`, а не нулевой задержкой или 100% покрытием.

Контейнер `langfuse-alerts` выполняет тот же скрипт в режиме `--watch` без зависимостей Python-агента, обращаясь только к `http://langfuse-web:3000` внутри локального Compose-проекта. Он пишет JSON-состояния в локальный журнал. Интервал можно настроить в `LANGFUSE_ALERT_INTERVAL_SECONDS`; по умолчанию 300. Просмотр:

```bash
cd vibe
docker compose --profile langfuse logs -f langfuse-alerts
```

Для уже работающего внешнего экземпляра можно запускать тот же checker явно с `--watch --interval-seconds 300 --env-file .env`. Скрипт сам не создаёт оповещения во внешних каналах и не управляет состоянием нативных алертов.

## Расследование

1. Скопируйте `run_id` из паспорта отчёта (для недоступного отчёта — UUID задания из запроса на проверку).
2. В Langfuse найдите Session с этим UUID. Просмотрите traces по попыткам: первоначальные стадии, ошибки, отмену или сбой сохранения, повтор с `cache_hit=true`.
3. Найдите красную стадию: имя эксперта, парный валидатор, Finalizer, адресная перепроверка, Drex, `archive`. Проверьте код ошибки и версию модели/промпта.
4. Сопоставьте роль и технический ID наблюдения с Evidence Graph отчёта; содержание и точные цитаты просматривайте в исходной системе, где проверяется доступ владельца.
5. Для повторения ошибки используйте ту же версию модели, релиз, режим ревью и обезличенный тестовый документ. Отсутствующая трасса при сетевом сбое не является подтверждением успеха.

Статус: экспорт проверяется локальными тестами с настоящим Python SDK, записывающим exporter и MockTransport; Docker Compose профиль можно проверить командой `docker compose --profile langfuse config --quiet`. Provisioning дашбордов и вычисление трёх алертов через Metrics v2 покрыты MockTransport-тестами, включая недоступность сервера и отсутствие данных. Дополнительно выполнен интеграционный smoke-test отдельного локального Compose-проекта 4.86.0 с временными ключами, внутренней Docker-сетью и синтетическими metadata-only трассами:

- Web healthcheck прошёл благодаря явной сетевой привязке Next.js. В изолированной internal-сети для проверки с хоста использовался временный loopback-only relay.
- Два запуска provisioning оставили ровно 5 дашбордов и 32 виджета; все 32 агрегированных запроса ответили HTTP 200.
- Все три порога сработали: 1 ошибка, 1 неполное покрытие и p95 856 500 мс. Одноразовая проверка вернула 1; контейнер `langfuse-alerts` последовательно записал `ALERT` в локальный журнал.
- После остановки и нового запуска всех семи контейнеров совпали идентификаторы всех дашбордов/виджетов, результаты метрик и состояние монитора.
- После расширения Model Evaluation и Review Operations выполнен второй независимый локальный smoke-test 4.86.0: синтетические вызовы двух моделей, двух режимов и двух точных хешей промпта; повторное применение оставило ровно 5 дашбордов / 51 виджет. Все 20 дополнительных запросов по моделям, хешам и счётчикам ответили HTTP 200 и вернули ожидаемые значения; возобновление = 1, переиспользование checkpoint = 2. Тесты реального durable restart и stale-lease recovery с имитацией LLM также подтверждают отсутствие resume-маркера на первом входе и ровно один на повторном.

В логах upstream worker 4.86.0 зафиксирована ошибка старой фоновой миграции `Cannot find module './addGenerationsCostBackfill'`. Для новой тестовой базы она не помешала экспорту, Scores, Metrics, дашбордам и проверке алертов; миграция существующей Langfuse-базы из более ранней версии не тестировалась.

Содержимое реальных ТЗ, рабочие ключи и внешние уведомления не использовались. Нативные алерты и каналы оповещения остаются опциональной настройкой в UI.
