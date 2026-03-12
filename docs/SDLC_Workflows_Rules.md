# SDLC & Workflows Rules

**Status:** `ACTIVE`
**Enforcement:** `MANDATORY`
**Scope:** All Executors (AI Agents & Human Engineers)

---

## 1. Global Imperatives & Constraints

Workflow enforces strict adherence to the **SSOT Architecture** defined in [Space Home]

### 1.2 Operational Mode

- **Single Thread Execution:** В системе разрешено наличие только одной Feature в активной фазе (статус `In Progress`).
- **Spec-First:** Исполнитель (Executor) не имеет права писать исполняемый код до прохождения этапа `Architecture Gate`.

#### 1.3 Strict Output Formats (Anti-Hallucination)

- **Format Locking:** Любой текстовый драфт (Drafting Phase) должен быть строго в формате **Markdown**, совместимом с Jira rendering. Использование HTML, XML или Rich Text запрещено, если не указано иное.
- **No Implicit Assumptions:** Исполнитель обязан использовать для поиска документации **только** значение из поля `Project`. Если Исполнитель не может найти директорию, по точному совпадению с этим полем, он ОБЯЗАН остановиться и вернуть ошибку Type B. "Похожие" названия использовать ЗАПРЕЩЕНО.
- **Prohibited Actions (Negative Constraints):**
  - Запрещено удалять существующий контент на страницах Confluence (только Append или Update конкретных секций).
  - Запрещено менять статус родительской Feature, если есть незакрытые блокирующие Task.
  - Если информация по атрибуту отсутствует, не определена или не может быть однозначно извлечена из контекста — генерация предположений запрещена. В поле значения необходимо явно указывать статус: `[DATA MISSING]` или `[NOT DEFINED]`

---

## 2. Naming Conventions & Taxonomy

### 2.1 Task Hierarchy (System Structure)

Система использует гибридную иерархию (Parent/Child + Links):

- **Parent (Type: Feature):** Единица поставки ценности.
  - **Custom Fields:**
    - **Components (optional):** Технический слой (front-end, back-end, blockchain, etc.). Используется для маршрутизации задач, но не определяет бизнес-контекст.
    - **Description (required):** Постановка задачи.
    - **Project (optional):** Уникальное имя директории проекта.
      - *Binding Rule:* Значение этого поля (например, `Exchange-Core`) = Название директории внутри Space Confluence по Key из Jira-Issue.
      - *Usage:* Исполнитель использует это значение для поиска директории и документов внутри.
    - **Project Link (optional):** Прямая ссылка на директорию проекта, в котором хранятся документы.

- **Child (Type: Story):** Техническая единица реализации. Создается Исполнителем автоматически после утверждения плана.
  - *Constraint:* Все технические задачи должны быть `Stories`, прилинкованными к `Feature` через поле `Parent Link`.

- **Blocking Entity (Type: Story — [PLAN REVIEW]):** Задача на проверку человеком (Review). Создаётся автоматически системой при завершении анализа.
  - *Linking Rule:* Feature **is blocked by** [PLAN REVIEW] Story.
  - *Reasoning:* Ревью — это внешняя зависимость, а не часть реализации. Пока [PLAN REVIEW] не в Done, Feature не может двигаться дальше.

### 2.2 Naming Standards

Исполнитель ОБЯЗАН соблюдать следующие форматы именования:

| Entity | Pattern | Example |
|--------|---------|---------|
| Feature Title | `[PROJECT] Short Description` | `[LP_Provider] Add auto-rebalance logic` |
| Story Title | `[LAYER] Technical Action` | `[BE] Implement RebalanceService` |
| Plan Review Task | `[PLAN REVIEW] {Issue_Key} Approve Architecture (HUMAN)` | `[PLAN REVIEW] AI-12 Approve Architecture (HUMAN)` |
| Code Review Task | `[PLAN REVIEW] Code Review (Iter {N})` | `[PLAN REVIEW] Code Review (Iter 1)` |
| Git Branch | `feature/<KEY>-<short-slug>` | `feature/AI-12-auto-rebalance` |
| Pull Request | `<KEY>: <Feature Title>` | `AI-12: [LP-Provider] Add auto-rebalance logic` |
| Draft Comment | `### DRAFT: {issue_key}_{slug}_{artifact_type}.md` | `### DRAFT: AI-12_rebalance_plan.md` |

### 2.3 Layer Taxonomy ([LAYER])

Исполнитель ОБЯЗАН использовать только значения из списка Allowlist. Если реализация Feature не требует изменений в конкретном слое, Story для других слоёв НЕ создается.

| Value | Context | Example |
|-------|---------|---------|
| BE | Backend, API, Microservices, Workers | `[BE] Create API Endpoint` |
| FE | Frontend, UI/UX implementation | `[FE] Implement React Component` |
| INFRA | Terraform, K8s, CI/CD pipelines | `[INFRA] Update Dockerfile` |
| DB | Migrations, SQL, Schema changes | `[DB] Add column to Users table` |
| QA | Tests (E2E, Integration), Automation | `[QA] Add Playwright scenario` |
| DOCS | Technical writers tasks (if separate) | `[DOCS] Update API Reference` |

**Fallback Rule:** Если задача не подходит ни под одну категорию, использовать `[GEN]` (General).

**Regex Layer Inference (автоматическое определение):** Система автоматически определяет слой по ключевым словам в описании задачи:
- `test`, `e2e`, `automation` → QA
- `migrat`, `schema`, `sql`, `database` → DB
- `deploy`, `terraform`, `k8s`, `ci/cd` → INFRA
- `document`, `readme`, `confluence` → DOCS
- `ui`, `frontend`, `component`, `react` → FE
- `api`, `endpoint`, `service`, `backend` → BE

### 2.4 Link Types

| Link Type | Jira Name | Direction | From | To | Usage |
|-----------|-----------|-----------|------|-----|-------|
| Blocking | Blocks | outward | PLAN REVIEW Story | Feature | Review блокирует Feature |
| Parent | Parent | outward | Feature | Story | Feature — родитель Story |
| Dependency | Blocks | outward | Prerequisite | Dependent | Зависимость между stories |

---

## 3. Board Configuration

### 3.1 Workflow Status Map

Семантика статусов обязательна для интерпретации Исполнителем:

| Status | Actor | Meaning |
|--------|-------|---------|
| Backlog | Human | Очередь задач. Триггер для Phase 0 (Backlog Analysis). |
| AI To Do | System | Входной буфер. Триггер для старта полного пайплайна (Stages 1–5). |
| Human Plan Review | Human | Blocking State. Ожидание утверждения архитектуры человеком. |
| Ready for Dev | System | Plan утверждён. Stories созданы и прилинкованы. Разрешен старт кодинга. |
| In Progress | Executor | Активная работа: написание кода и тестов. |
| Review | Human | Blocking State. Код написан, PR создан. Ожидание Merge. |
| Deployment | Executor | Техническая фаза. Выполнение Merge, CI проверок и закрытие задач. |
| Done | System | Задача выполнена, код влит в `main`. |

### 3.2 Status Gate — автоматическая маршрутизация

При вызове `execute.py --task ISSUE_KEY` система автоматически определяет маршрут на основе текущего статуса:

| Текущий статус | Маршрут |
|----------------|---------|
| Backlog | → Phase 0 (Backlog Analysis) |
| AI To Do | → Полный пайплайн (Stages 1–5) |
| Human Plan Review | → Plan Review Handler (создание Stories) |
| Другой статус | → Status Checker (pre-flight fulfillment) |

---

## 4. The Pipeline Protocol

### Phase 0: Backlog Analysis (Backlog → AI To Do)

**Trigger:** Feature в статусе `Backlog`. Запуск: `python execute.py --task PROJ-123 --phase0`

**Constraint (Read-Only):** В этой фазе Исполнителю СТРОГО ЗАПРЕЩЕНО вносить любые изменения на страницы Confluence. Все операции — только чтение.

**Context Loading:**
1. Считать значение KEY тикета Jira
2. Считать значение полей Project, Project Link и Component из тикета Jira
3. Используя эти значения, найти "Project Passport" и "Logical Architecture" в Confluence
4. В случае если значение поля Project Link не заполнено — считать это новым Project

**LLM Analysis (DeepSeek):**

Система генерирует структурированный анализ, содержащий:
1. **Chain of Thought:** Requirements → System Actions
2. **Use Case:** Actors, preconditions, flow, postconditions
3. **Work Areas by Layer:** BE, FE, INFRA, DB, QA, DOCS
4. **Risks:** С оценкой серьёзности
5. **Clarification Questions:** Для недостающих данных (BLOCKING и INFORMATIONAL)
6. **Complexity Estimate:** S, M, L, XL
7. **Definition of Ready:** Тестируемые критерии

**Output:**
- Файл: `{ISSUE_KEY}_phase0.md`
- Jira description обновляется через ADF expand block "Phase 0: Requirements Analysis"

#### Phase 0.5: Feedback Incorporation (автоматическое определение)

**Auto-Detection:** При повторном запуске `--phase0` система автоматически определяет Phase 0.5, если:
1. Jira description содержит "Phase 0: Requirements Analysis" expand block
2. Assignee оставил комментарии после Phase 0 timestamp

**Feedback Filter:** Учитываются ТОЛЬКО комментарии от assignee (фильтрация по `assignee_account_id`). Комментарии от других пользователей игнорируются.

**Logic:**
- Загружает предыдущий анализ из `_phase05.md` (приоритет) или `_phase0.md` (fallback)
- Re-evaluates Definition of Ready с учётом ответов assignee
- **Auto-Transition:** Если все BLOCKING вопросы разрешены → автоматический переход в `AI To Do`

**Output:** Файл: `{ISSUE_KEY}_phase05.md`

---

### Phase 1: Analysis & Context Loading (AI To Do → Human Plan Review)

**Trigger:** Feature в статусе `AI To Do`. Запуск: `python execute.py --task PROJ-123`

Система выполняет автоматизированный 5-стадийный пайплайн:

#### Stage 1 — Trigger (Parse & Validate Issue Key)
- Парсит и валидирует ключ задачи (формат: `^[A-Z][A-Z0-9]+-\d+$`)
- Принимает как ключ (`PROJ-123`), так и Jira URL

#### Stage 1.5 — Status Gate (Auto-Route by Status)
- Определяет текущий статус задачи в Jira
- Маршрутизирует выполнение (см. таблицу в разделе 3.2)

#### Stage 2 — Jira Enrichment (Extract Task Context)
Извлекает из Jira:
- Identity: issue_key, issue_id
- Core: summary, description (Markdown), issue_type, status
- Project: project_key, project_name
- Classification: components, labels
- People: assignee, assignee_account_id, reporter
- Hierarchy: parent_key, subtasks
- Comments: author, created, body
- Confluence context: confluence_space_key, project_folder, project_link

#### Stage 3a — Confluence Knowledge (Two-Stage Retrieval)

**Stage 1 — API Search & Fetch Mandatory Docs:**
- Получает Project Passport (mandatory)
- Получает Logical Architecture (mandatory)
- Получает SDLC Rules page
- Content Budget: **12,000 chars** на core doc

**Stage 2 — LLM Filtering (DeepSeek):**
- Вызывает DeepSeek для rerank supporting Confluence pages
- Выбирает только implementation-relevant docs (API specs, architecture decisions, contracts)
- Content Budget: **6,000 chars** на supporting doc
- Результат: `RefinedConfluenceContext` с `SelectionLog`

**Project Status Detection:**

| Status | Описание | Поведение |
|--------|----------|-----------|
| EXISTING | Passport + Architecture с контентом | Полный контекст |
| INCOMPLETE | Страницы есть, но пустые | LLM генерирует шаги для заполнения |
| NEW_PROJECT | Папка есть, mandatory docs нет | LLM генерирует шаги создания |
| NOT_FOUND | Папка не существует | Ошибка контекста |
| BRAND_NEW | Нет project_link И нет project_folder | Greenfield — LLM включает шаги создания |

#### Stage 3b — GitHub Context
- Обнаружение: ищет GitHub URL в description + comments задачи
- Извлекает: структуру репозитория, конфиги, последние коммиты (max 10), открытые PR (max 10), ключевые файлы (max 5), primary language
- **Deduplication:** пропускает топики, уже покрытые в Confluence (tech_stack, architecture, api_contracts, database, deployment)

#### Stage 3c — Template Compliance
- Получает шаблоны из папки "Templates/Patterns" в Confluence
- Инжектирует шаблоны в LLM промпт для соблюдения структуры документов

#### Stage 4 — Data Aggregation
- Объединяет все контексты в `ExecutionContext`: Jira + Confluence (refined) + GitHub + Templates
- Формирует промпт-контекст для LLM

#### Stage 5 — LLM Execution (DeepSeek)

**Configuration:**
- Model: `deepseek-chat`
- Temperature: `0.2`
- Max Tokens: `8,192`
- API Retry: 3 retries с exponential backoff (429, 502, 503, 504)

**Response Format (5 обязательных секций):**
```
### 1. Understanding
### 2. Concerns
### 3. Analysis
### 4. Work Plan
### 5. Definition of Ready
```

**Work Plan Format (валидируется):**
```
- [ ] **Step N:** Description
  - **Specification:** Goal: X | Outcome: Y
  - **Layer:** [BE/FE/INFRA/DB/QA/DOCS/GEN]
  - **Files:** file1.ts, file2.py
  - **Acceptance:** Criteria for success
  - **Depends on:** Step X, Step Y (optional)
```

**Validation Pipeline (автоматическое исправление):**
1. Attempt 1: полный промпт LLM
2. При невалидности: regex post-processing (инферит Layer по ключевым словам, добавляет недостающие Fields/Acceptance)
3. Если ещё невалидно: LLM retry с targeted fix prompt
4. Max retries: 2 (3 попытки итого)
5. Fallback: proceed с warnings

**Validation Rules:**

| Правило | Уровень | Описание |
|---------|---------|----------|
| Content exists | Error | Work Plan >50 chars |
| Steps present | Error | Минимум 1 step (pattern: `- [ ] **Step N:**`) |
| Layer tags | Error | Каждый step имеет Layer (BE/FE/INFRA/DB/QA/DOCS/GEN) |
| Files field | Error | Каждый step имеет **Files:** (non-empty) |
| Acceptance field | Error | Каждый step имеет **Acceptance:** (non-empty) |
| Step count | Warning | >15 steps → warning |
| Sequential numbering | Warning | Номера steps последовательны |
| Vague acceptance | Warning | "should work properly", "as expected" → warning |

#### Post-Execution (автоматические действия)

**При успехе (SUCCESS):**
1. Создаёт blocking review Story: `[PLAN REVIEW] {issue_key} Approve Architecture (HUMAN)`
2. Связь: Feature **is blocked by** [PLAN REVIEW] Story
3. Публикует consolidated ADF comment в Jira с 4 expand blocks:
   - **Context Summary:** информация о задаче, документы, репозиторий
   - **Technical Decomposition:** таблица stories с layer, files, acceptance, dependencies
   - **Executor Rationale:** Chain of Thought (Context, Decision, Alternatives Discarded)
   - **Clarification Questions:** вопросы (если есть)
4. Переводит Feature в статус `Human Plan Review`

**Decomposition & Confidence Scoring:**
- Каждая Story получает confidence score (0.0–1.0) на основе полноты контекста
- Stories с confidence <70% помечаются флагами в Jira comment
- Общая confidence по Feature рассчитывается как среднее

**При ошибке контекста (CONTEXT_ERROR):**
- Переводит Feature из `AI To Do` → `Backlog`
- Публикует комментарий с описанием ошибки и required actions

**Output Files (в `outputs/{ISSUE_KEY}/`):**

| Файл | Описание |
|------|----------|
| `{KEY}_context.md` | Raw context (Stages 2–4) |
| `{KEY}_selection.md` | Document selection log (Two-Stage Retrieval) |
| `{KEY}_prompt.md` | Полный LLM промпт |
| `{KEY}_reasoning.md` | Полный LLM response + metadata |
| `{KEY}_plan.md` | Извлечённый Work Plan |
| `{KEY}_metrics.md` | Token usage, timing, retries |
| `{KEY}_context_store.json` | Serialized context (для `--refine`) |

---

### Phase 2: Human Plan Review (Human Plan Review → Ready for Dev)

**Status:** `Human Plan Review`

#### Scenario A: Approval (Happy Path)

1. Человек изучает Technical Decomposition в Jira comment
2. Человек закрывает [PLAN REVIEW] Story (статус `Done`)
3. Запуск: `python execute.py --task PROJ-123`
4. Система автоматически определяет статус `Human Plan Review`
5. Проверяет: [PLAN REVIEW] task в статусе `Done`
6. **Story Creation (автоматически):**
   - Извлекает stories из Technical Decomposition comment
   - Создаёт Jira Story issues (idempotent — пропускает уже существующие)
   - Устанавливает `Parent Link` = Feature для всех Stories
   - Создаёт dependency links (Blocks) между зависимыми Stories
   - **Duplicate Detection:** manifest file + JQL по существующим children
7. Переводит Feature в статус `Ready for Dev`

**Story Summary Format:** `[{layer}] {title}`

#### Scenario B: Denial (Bad Path)

Человек возвращает Feature в статус `AI To Do` с комментарием. Исполнитель обязан переработать план (возврат к Phase 1).

---

### Phase 2.5: Refinement (без MCP, --refine)

**Trigger:** Запуск: `python execute.py --refine PROJ-123 --feedback "Split step 3 into BE and FE"`

**Назначение:** Re-run Stage 5 (LLM) с human feedback без пересбора контекста.

**Logic:**
1. Загружает context из `{KEY}_context_store.json` (Stages 1–4 не выполняются)
2. Вызывает LLM с оригинальным контекстом + feedback
3. Генерирует новую версию Work Plan: `{KEY}_plan_v2.md`, `{KEY}_plan_v3.md`, ...
4. Человек обновляет Jira comment с refined планом

---

### Phase 3: Development

**Trigger:** Feature перешла в статус `In Progress` (ручной запуск Исполнителя).

#### 3.1 Initialization Step (Branching Logic)

Исполнитель проверяет наличие удаленной ветки `feature/<KEY>-...` в Git.

**CASE A: Greenfield (Start New Feature)**

**Condition:** Ветка НЕ существует.

- **SSOT Sync:** Найти последний по времени комментарий с заголовком `### DRAFT:...`, оставленный перед переходом в статус In Progress. Обновить страницу Confluence `[Project]/Logical Architecture` (или `Project Passport`), полностью заменив её содержимое утвержденным Markdown-текстом.
- **Git Setup:** Создать ветку `feature/<KEY>-...` от `main`.

**CASE B: Rework (Fix Existing Feature)**

**Condition:** Ветка УЖЕ существует.

- **Context Loading:** Считать комментарии из последней закрытой задачи `[PLAN REVIEW]`.
- **Git Setup:** Выполнить `git checkout feature/<KEY>-...` и `git pull`.
- **Constraints:** Confluence НЕ обновлять. Новые Stories НЕ создавать.

#### 3.2 Core Execution Loop (Iterative)

**Scope:** Все Stories, привязанные к Feature, которые не находятся в статусе Done.

For Each Story in Scope:
1. **Check:** Если Story требует уточнения — пропустить.
2. **Test:** Написать тест, воспроизводящий требование (TDD).
3. **Implement:** Написать код, проходящий тест.
4. **Commit:** Закоммитить изменения с сообщением `"<Story_Key>: <Action>"`.
5. **Status:** Оставить комментарий в Story: `"Implemented in commit <hash>"`. (Саму Story в Done не переводить!)

#### 3.3 Finalization & Handover (Exit Gate)

1. **Push:** Отправить ветку в origin.
2. **PR Creation:** Создать (или обновить) Pull Request в `main`. В описании PR добавить ссылку на Feature.
3. **Blocking:** Создать задачу `[PLAN REVIEW] Code Review (Iter N)`.
4. **Transition:** Перевести Feature в статус `Review`.

---

### Phase 4: Review & Finalization

**Status:** `Review` → `Deployment`

**Pre-condition:** Код написан, PR создан, Feature заблокирована задачей `[PLAN REVIEW] Code Review`.

#### Scenario A: Request Changes (Reject Loop)

**Trigger:** Человек находит ошибки и возвращает задачу на доработку.

**Executor Actions:**
1. Считывает комментарии из Jira и Git
2. Исправляет код (Local → Commit → Push)
3. Отвечает на комментарии ("Fixed in commit <hash>")
4. Создает **новый** блокирующий Task с инкрементом итерации: `[PLAN REVIEW] Code Review (Iter N+1)`
5. Переводит Feature в статус `Review`

#### Scenario B: Approval & Merge (Happy Path)

**Trigger:** Человек утверждает изменения (Approve в GitHub) и переводит Feature в `Deployment`.

**Executor Actions (Deployment Phase):**
1. Проверяет наличие Approve в PR
2. Выполняет Merge PR в ветку `main`
3. Проверяет статус CI Pipeline после мержа

#### Scenario C: Final Cleanup (System Finalization)

**Status:** `Deployment` → `Done`

**Trigger:** Merge прошел успешно и CI зеленый.

**Executor Actions:**
1. **Close Children:** Переводит все дочерние Stories в статус `Done`
2. **Unblock:** Переводит блокирующую задачу `[PLAN REVIEW] ...` в `Done`
3. **Close Parent:** Переводит саму Feature из `Deployment` в статус `Done`
4. **Artifacts:** Публикует ссылку на PR и версию релиза в комментарии Jira

---

## 5. Error Handling & Logging

### 5.1 Chain of Thoughts (CoT)

Исполнитель обязан логировать ключевые решения. При автоматическом выполнении (Phase 0, Phase 1) система публикует CoT в ADF expand block "Executor Rationale" с полями:

- **Context:** описание входных данных и контекста
- **Decision:** принятое решение
- **Alternatives Discarded:** отвергнутые альтернативы

В ручных фазах (Phase 3, 4) Исполнитель использует Jira panel:
```
{panel:title=Executor Rationale|borderColor=#ccc}
**Context:** ...
**Decision:** ...
**Alternatives Discarded:** ...
{panel}
```

### 5.2 Error Classification Strategy

Исполнитель обязан классифицировать ошибку перед действием:

**Type A: Deterministic / Logic Errors (Internal)**
- **Definition:** Ошибки в коде, тестах, синтаксисе, невалидный LLM response.
- **Action:** `Self-Correction`. Исполнитель обязан попытаться исправить ошибку (до 3 итераций).
- **LLM Validation Recovery:** regex post-processing → LLM retry с targeted fix prompt → fallback с warnings.
- **Result:** Если ошибка повторяется — создать задачу на Человека.

**Type B: Environmental / Access Errors (External)**
- **Definition:** Ошибки инфраструктуры (401/403, API Down, Missing Config), отсутствие контекста в Confluence/GitHub.
- **Action:** `Escalation`. Немедленная остановка. Feature переводится в `Backlog` с комментарием об ошибке.
- **LLM API Transient Errors:** 429, 502, 503, 504 — автоматический retry с exponential backoff (2s, 4s, 8s, max 3 retries).

### 5.3 Pipeline Metrics

Система собирает метрики на каждом этапе:
- **Stage Metrics:** duration_ms, success/error
- **LLM Call Metrics:** tokens_in, tokens_out, model, call_purpose (planning/retry/refinement), attempt_number, validation_attempts, validation_errors
- Метрики сохраняются в `{KEY}_metrics.md`

---

## 6. Quality Gates

### 6.1 Definition of Ready (DoR) — Entry Gate

- [ ] **Clear Goal:** Описание задачи однозначно интерпретируется.
- [ ] **Decomposition Clarity:** Исполнитель понимает, какие технические шаги (Story) потребуются.
- [ ] **Resources Located:** Найдены и доступны релевантные страницы Confluence.
- [ ] **Resources Located:** Найден и доступен (read access) релевантный GitHub репозиторий (или помечен как new project).
- [ ] **Codebase Alignment:** Предложенный подход соответствует существующим паттернам кодовой базы.

**Phase 0 DoR:** Генерируется автоматически системой. Содержит два типа критериев:
- **BLOCKING:** Требуют разрешения перед переходом в AI To Do (например: "What is the API contract?")
- **INFORMATIONAL:** Только для контекста, не блокируют переход (например: "Any UI mockups?")

### 6.2 Architecture Gate — Transition Gate

- [ ] План изменений зафиксирован в Jira Comment (Technical Decomposition)
- [ ] Человек закрыл `[PLAN REVIEW] Approve Architecture` как `Done`
- [ ] Stories созданы и прилинкованы к Feature (автоматически)

### 6.3 Definition of Done (DoD) — Exit Gate

- [ ] **SSOT:** Страница Confluence соответствует реализации.
- [ ] **Code:** PR влит (Merged) в `main`.
- [ ] **Quality:** CI Pipeline зеленый (Tests Passed).
- [ ] **Clean-up:** Все дочерние Story закрыты (`Done`).

---

## 7. Technical Appendices

### 7.1 Git Branching Protocol

Исполнитель обязан следовать строгому алгоритму:

1. **Sync Master:** `git checkout main` → `git pull origin main`.
2. **Create Feature Branch:** `git checkout -b feature/<KEY>-<name>`.
3. **Implementation Loop:** Write Code → Write Tests → Commit.
4. **Local Integration Check:** `git fetch origin main` → Rebase/Merge main → Verify Conflicts.
5. **Push & PR:** `git push origin feature/<KEY>-<name>` → Create PR to `main`.

### 7.2 CLI Reference

| Команда | Описание |
|---------|----------|
| `python execute.py --task PROJ-123` | Полный пайплайн (auto-route по статусу) |
| `python execute.py --task PROJ-123 --phase0` | Phase 0: Backlog Analysis |
| `python execute.py --task PROJ-123 --dry-run` | Stages 1–4 only, без LLM |
| `python execute.py --task PROJ-123 --force` | Bypass pre-flight artifact check |
| `python execute.py --task PROJ-123 --output-dir ./out` | Указать директорию output |
| `python execute.py --refine PROJ-123 --feedback "..."` | Re-run Stage 5 с feedback |

### 7.3 MCP Integration

Три MCP сервера, управляемые `MCPClientManager`:

| Сервер | Тип | Назначение |
|--------|-----|-----------|
| Jira | Custom Python (`jira_server.py`) | ADF↔Markdown conversion, custom fields, issue links |
| Confluence | Custom Python (`confluence_server.py`) | CQL search, page content, ADF documents |
| GitHub | Official (`@modelcontextprotocol/server-github`) | Repo structure, commits, PRs (via npx, optional) |

### 7.4 Confluence Document Structure

**Mandatory Core Documents (per project):**

**Project Passport** — обязательные поля:
- Identity & Ownership
- Technology Stack
- Repositories
- Environments

**Logical Architecture** — обязательные поля:
- Component Diagram
- Data Flow
- Contracts & Interfaces
- Constraints

### 7.5 Idempotency Guarantees

Система обеспечивает идемпотентность на всех уровнях:
- **[PLAN REVIEW] Story:** Пропускается если уже существует (checked via issuelinks)
- **Child Stories:** Checked via manifest + JQL; пропускаются если уже созданы
- **Dependency Links:** Jira игнорирует дублирующиеся links

### 7.6 Configuration

**Environment (`.env`):** Atlassian credentials, DeepSeek API key, GitHub token

**Workflow config (`config/sdlc_config.yaml`):**
- Confluence structure (space, pages)
- Jira workflow statuses, issue types, link types
- Layer taxonomy
- Naming patterns
- Quality gates
- Error handling settings
- LLM parameters (model: `deepseek-chat`, temperature: 0.2, max_tokens: 8192)
- Content budgets (core: 12,000 chars, supporting: 6,000 chars)
- API resilience (timeout: 120s, max retries: 3, backoff: 2s base)
