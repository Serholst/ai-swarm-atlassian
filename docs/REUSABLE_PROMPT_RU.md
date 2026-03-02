# Контекст проекта AI-Swarm

Вставь этот текст в начало нового диалога с Claude Code, затем добавь свою цель.

---

## Что делает проект

AI-Swarm — CLI-инструмент, который автоматизирует подготовку задач к разработке. Берёт задачу из Jira (от размытой идеи в бэклоге) и превращает её в структурированный план работ с конкретными дочерними историями, готовыми к передаче команде.

Система интегрируется с тремя платформами через MCP-серверы (Model Context Protocol): Jira, Confluence и GitHub. Для анализа и генерации планов используется DeepSeek LLM (через OpenAI-совместимый API).

---

## Бизнес-процесс (от начала до конца)

### Phase 0 — Анализ бэклога (статус Jira: Backlog)

Вход: сырая задача в Jira с минимальным описанием.

Система:
1. Собирает контекст из полей Jira и документации проекта в Confluence
2. Отправляет контекст в LLM, который выдаёт: классификацию типа задачи (`new_feature`, `update_existing`, `documentation_only`, `process`), use cases, рабочие области по слоям, Definition of Ready (DoR) с BLOCKING/NON-BLOCKING вопросами
3. Валидирует ответ LLM — если критические секции (use_case, definition_of_ready) отсутствуют, делает одну повторную попытку; если по-прежнему невалидно — НЕ записывает в Jira и завершается с ошибкой
4. Записывает анализ в описание задачи Jira как ADF expand-блоки
5. Публикует уточняющие вопросы комментарием в Jira для назначенного

### Phase 0.5 — Учёт обратной связи (автообнаружение)

Срабатывает автоматически при повторном запуске `--phase0`, когда система обнаруживает:
- Существующий анализ Phase 0 в описании задачи
- Новые комментарии от назначенного (фильтрация по `assignee_account_id` — комментарии не от назначенного игнорируются)

Система переоценивает DoR. Если все BLOCKING-вопросы закрыты — автоматически переводит задачу в статус "AI To Do".

### Полный pipeline — Генерация плана работ (статус Jira: AI To Do)

Пятиэтапный конвейер:

**Stage 1 — Триггер:** Парсинг и валидация ключа задачи из CLI.

**Stage 1.5 — Шлюз статуса:** Получает статус задачи и маршрутизирует:
- Backlog → Phase 0
- AI To Do → полный pipeline (Stages 2–5)
- Human Plan Review → проверка [PLAN REVIEW], создание историй при утверждении
- Остальные статусы → status_checker (валидация артефактов)

**Stage 2 — Обогащение из Jira:** Получение деталей задачи в `JiraContext`: summary, description, поля, комментарии, метки, компоненты, назначенный, иерархия parent/subtask.

**Stage 3a — Знания из Confluence (двухэтапная выборка):**
1. Получение обязательных базовых документов: Project Passport, Logical Architecture
2. Поиск вспомогательных документов через CQL, затем LLM-фильтрация (ранжирование) по релевантности к задаче
3. Получение шаблонов Confluence из папки "Templates/Patterns" для контроля структуры документов

**Stage 3b — Контекст GitHub (условный):**
Gate-функция `should_fetch_github_context()` решает, нужно ли обращаться к GitHub:
- Если задача не связана с кодом (метки `no-code`/`docs-only`/`process`, или feature_type `documentation_only`/`process`) → пропуск
- Ищет URL репозитория по источникам в порядке приоритета: описание Jira → комментарии назначенного → кастомное поле Jira (`project_link`) → Confluence Passport
- Если URL не найден нигде → пропуск
- Если URL на уровне задачи отличается от URL на уровне проекта (Confluence Passport) → используется URL задачи с логированием переопределения
- При обращении: получает структуру репозитория, конфигурационные файлы, фрагменты кода, последние коммиты, открытые PR
- Дедупликация с Confluence: если темы (tech_stack, architecture, api_contracts, database, deployment) уже задокументированы — эти данные из GitHub не подтягиваются

**Stage 4 — Агрегация данных:** Объединение всех контекстов в `ExecutionContext` — единый объект, передаваемый в LLM.

**Stage 5 — Выполнение LLM:** Построение промта из `ExecutionContext`, вызов DeepSeek с retry-логикой (экспоненциальный backoff на 429/502/503/504), валидация структуры ответа, извлечение плана работ и декомпозиции историй. Цикл повторных попыток валидации (до 2 попыток) исправляет некорректные ответы через целевое повторное промптирование.

**Post-Execution (автоматически после Stage 5):**
При успешном завершении pipeline:
1. Выполняется Analysis & Decomposition — парсинг ответа LLM, извлечение декомпозиции историй
2. Создаётся блокирующая задача `[PLAN REVIEW] {KEY} Approve Architecture (HUMAN)` с Blocks-связью к родительской задаче (идемпотентно — если уже существует, пропускается)
3. Публикуется сводный ADF-комментарий с блоками: Context Summary, Technical Decomposition, Executor Rationale, Clarification Questions
4. Задача автоматически переводится из "AI To Do" → "Human Plan Review"

При неуспешном завершении: задача возвращается в "Backlog" с комментарием об ошибке.

### Жизненный цикл задачи по статусам

```
Backlog                    ← Phase 0 анализирует, задаёт вопросы
  ↓ (Phase 0.5: все BLOCKING-вопросы закрыты)
AI To Do                   ← Полный pipeline (Stages 1–5) генерирует план
  ↓ (Post-Execution: pipeline успешен, создан [PLAN REVIEW])
Human Plan Review          ← Человек проверяет план, утверждает [PLAN REVIEW] → Done
  ↓ (--task: обнаруживает [PLAN REVIEW] Done, создаёт истории)
Ready for Dev              ← Дочерние истории созданы, задача готова к разработке
  ↓
In Progress → Review → Deployment → Done
```

Все переходы от Backlog до Ready for Dev управляются через `--task`. Остальные переходы (In Progress → Done) управляются командой вручную.

При запуске `--task` на задаче в статусе "Human Plan Review" система проверяет, находится ли задача [PLAN REVIEW] в статусе Done. Если утверждена — создаёт дочерние истории и переводит в "Ready for Dev". Если ещё не утверждена — информирует пользователя и завершается. Для статусов после "Ready for Dev" запускается `status_checker`.

### Создание историй (автоматически через --task на "Human Plan Review")

Запускается автоматически, когда `--task` выполняется на задаче в статусе "Human Plan Review" и задача [PLAN REVIEW] в статусе Done.

Процесс:
1. Проверка, что [PLAN REVIEW] утверждён (Done) — если нет, информирует пользователя и завершается
2. Повторное извлечение историй из комментария Technical Decomposition
3. Проверка существующих дочерних историй для предотвращения дублей (идемпотентность)
4. Создание Jira Story с корректной иерархической связью к родительскому Feature
5. Каждая история помечена слоем: BE, FE, INFRA, DB, QA, DOCS, GEN
6. Манифест создания отслеживает результаты — при прерывании повторный запуск продолжает с места остановки
7. Частичные сбои (часть историй создана, часть нет) дают exit code 2 — переход не выполняется
8. При полном успехе родительская задача переводится в "Ready for Dev"

### Уточнение (Refinement)

Повторный запуск Stage 5 с текстовой обратной связью, без повторного обращения к MCP:
```
python execute.py --refine PROJ-123 --feedback "Разбей шаг 3 на BE и FE"
```
Использует сериализованный `ExecutionContext` из `context_store.json`.

---

## Механизмы безопасности

**Блокировка по задаче:** Файловая блокировка на каждый ключ задачи (`outputs/.locks/{KEY}.lock`). Предотвращает одновременный запуск pipeline на одну задачу.

**Атомарная запись файлов:** Все выходные файлы записываются через `atomic_write()` — запись в `.tmp`, затем `Path.replace()` (атомарная на POSIX).

**Retry для LLM API:** Транзиентные ошибки (429, 502, 503, 504) вызывают экспоненциальный backoff: 2с → 4с → 8с, до 3 попыток. Настраивается через `sdlc_config.yaml`.

**Валидационный шлюз Phase 0:** Ответы LLM валидируются перед записью в Jira. Критические ошибки блокируют запись — сырой ответ сохраняется в `outputs/` для отладки, но Jira не модифицируется.

**Идемпотентность создания историй:** Обнаружение дублей по совпадению summary дочерних историй. Файл-манифест (`_stories_manifest.json`) отслеживает результаты для возобновления после сбоя.

**Верификация переходов Jira:** После POST-запроса на переход статуса система проверяет, что задача действительно достигла целевого статуса.

---

## CLI точки входа

```bash
# Полный pipeline (автомаршрутизация по статусу Jira — единая точка входа)
python execute.py --task PROJ-123
python execute.py --task PROJ-123 --dry-run      # пропустить вызов LLM
python execute.py --task PROJ-123 --force         # обойти предварительные проверки
python execute.py --task PROJ-123 --output-dir ./out

# Phase 0 (Backlog → требования + DoR)
python execute.py --phase0 PROJ-123
# Повторный запуск автоматически обнаруживает Phase 0.5, если есть обратная связь

# Уточнение (повторный запуск Stage 5 с фидбеком, MCP не нужен)
python execute.py --refine PROJ-123 --feedback "Разбей шаг 3 на BE и FE"
```

---

## Ключевые модули

| Модуль | Назначение |
|--------|-----------|
| `execute.py` | CLI точка входа, оркестрация pipeline, маршрутизация по статусу, блокировка задач |
| `src/executor/phases/context_builder.py` | Stages 1–4: обогащение из Jira, выборка из Confluence, GitHub gate + контекст, агрегация |
| `src/executor/phases/llm_executor.py` | Stage 5: вызовы LLM API с retry, валидация ответов, генерация выходных файлов |
| `src/executor/phases/phase_zero.py` | Phase 0 + Phase 0.5: анализ бэклога, учёт обратной связи, валидационный шлюз |
| `src/executor/phases/story_creator.py` | Идемпотентное создание Jira-историй с манифестом и обнаружением дублей |
| `src/executor/phases/decomposition.py` | Извлечение историй из ответа LLM, таксономия слоёв (BE/FE/INFRA/DB/QA/DOCS/GEN) |
| `src/executor/phases/validation.py` | Правила валидации плана работ (структура, зависимости, качество) |
| `src/executor/phases/post_execution.py` | Пост-обработка: комментарии Jira, переходы статусов, создание задачи [PLAN REVIEW] |
| `src/executor/phases/status_checker.py` | Валидация артефактов по статусу и автоисправление |
| `src/executor/phases/context_store.py` | Сериализация/десериализация `ExecutionContext` для режима `--refine` |
| `src/executor/mcp/client.py` | Менеджер MCP-клиентов — жизненный цикл серверов Jira, Confluence, GitHub |
| `src/executor/mcp/servers/jira_server.py` | Кастомный MCP-сервер Jira (REST API, конвертация ADF↔Markdown) |
| `src/executor/mcp/servers/confluence_server.py` | Кастомный MCP-сервер Confluence (CQL-поиск, получение страниц) |
| `src/executor/models/execution_context.py` | Основные датаклассы: `ExecutionContext`, `JiraContext`, `RefinedConfluenceContext` |
| `src/executor/models/github_models.py` | GitHub датаклассы: `GitHubContext`, `GitHubFetchDecision`, `RepoStatus` |
| `src/executor/prompts/system_prompt.py` | Системный промт для DeepSeek |
| `src/executor/prompts/user_prompt.py` | Пользовательский промт с инъекцией контекста |
| `src/executor/prompts/phase_zero_prompt.py` | Шаблоны промтов Phase 0 |
| `src/executor/prompts/phase_zero_feedback_prompt.py` | Шаблоны промтов Phase 0.5 |
| `src/executor/utils/issue_lock.py` | Файловая блокировка задач (`acquire_issue_lock`) |
| `src/executor/utils/file_utils.py` | Утилита `atomic_write()` |
| `src/executor/utils/config_loader.py` | Загрузчик YAML-конфигурации |

---

## Поток данных

```
JiraContext + RefinedConfluenceContext + GitHubContext
    → ExecutionContext
        → LLM-промт (системный + пользовательский)
            → Ответ LLM
                → Валидация
                    → План работ + DecompositionResult
                        → Дочерние Jira Story (BE/FE/INFRA/DB/QA/DOCS/GEN)
```

---

## Конфигурация

**Окружение (`.env`):** URL Atlassian, учётные данные бота/админа (email + API-токен), ключ API DeepSeek, токен GitHub.

**Конфиг рабочего процесса (`config/sdlc_config.yaml`):** Структура Confluence (пространство, заголовки страниц), статусы Jira-воркфлоу и маппинг кастомных полей, настройки извлечения из GitHub (конфигурационные файлы, ключевые директории, лимиты коммитов/PR, темы дедупликации), таксономия слоёв, параметры LLM (модель: `deepseek-chat`, температура: 0.2, таймаут: 120с, retry: 3 попытки), quality gates (DoR, architecture gate, DoD).

---

## Выходные файлы

Генерируются в `outputs/{ISSUE_KEY}/`:

- `{KEY}_context.md` — агрегированный контекст, отправленный в LLM
- `{KEY}_prompt.md` — полный промт для LLM
- `{KEY}_reasoning.md` — сырой ответ LLM
- `{KEY}_plan.md` — извлечённый план работ
- `{KEY}_metrics.md` — использование токенов, тайминг, информация о модели
- `{KEY}_selection_log.md` — логика выбора документов Confluence
- `{KEY}_phase0.md` — анализ Phase 0
- `{KEY}_phase05.md` — обновлённый анализ Phase 0.5
- `{KEY}_context_store.json` — сериализованный ExecutionContext для `--refine`
- `{KEY}_stories_manifest.json` — результаты создания историй для идемпотентности

---

## Стиль кода

- Python 3.11+
- Длина строки: 100 (Black + Ruff)
- Проверка типов: MyPy в строгом режиме
- Модели данных: Pydantic v2 и dataclasses
- Тесты: pytest (`tests/unit/` для юнит-тестов, `tests/test_mcp_integration.py` для интеграционных)

---

## Известные ограничения (ещё не исправлены)

- Нет механизма напоминания/таймаута, если назначенный не отвечает на вопросы Phase 0
- Комментарии не от назначенного (тимлид, QA) игнорируются в Phase 0.5
- Нет отслеживания частичного прогресса по DoR (бинарно: все BLOCKING-вопросы закрыты или нет)
- Нет кросс-валидации файлов, на которые ссылается LLM, с реальной структурой репозитория
- Пагинация Confluence ограничена ~20 документами на запрос
- Созданные дочерние истории не наследуют поля родителя (assignee, sprint, priority, labels)
- Нет лимита на количество историй в декомпозиции
- Нет оценки трудозатрат (story points / t-shirt sizing) для сгенерированных историй
- Несовпадение версии context store только логирует предупреждение, не падает и не мигрирует

---

## Цель

[Опиши здесь свою задачу]
