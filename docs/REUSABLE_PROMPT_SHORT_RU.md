# AI-Swarm — Краткий контекст

Перед началом работы прочитай `docs/PROJECT_CONTEXT_RU.md` — там полное описание бизнес-логики, модулей, механизмов безопасности и известных ограничений. Также прочитай `CLAUDE.md` в корне проекта.

## Суть проекта

CLI-инструмент (`execute.py`), который автоматизирует SDLC через Jira + Confluence + GitHub (MCP-серверы) и DeepSeek LLM.

## Единая точка входа

`python execute.py --task PROJ-123` — маршрутизирует по статусу Jira:

```
Backlog → Phase 0 (анализ + DoR)
  ↓ Phase 0.5 (все BLOCKING закрыты)
AI To Do → Stages 1–5 (контекст → LLM → план) → [PLAN REVIEW] создан → Human Plan Review
  ↓ [PLAN REVIEW] Done
Human Plan Review → создание историй → Ready for Dev
  ↓
In Progress → ... → Done (вручную)
```

## Критические правила

- Все переходы Backlog → Ready for Dev через `--task` (нет отдельных команд для создания историй)
- GitHub context условный: gate-функция проверяет, нужен ли код (метки, feature_type, наличие URL репо)
- Все файловые записи через `atomic_write()`, все pipeline-запуски под `acquire_issue_lock()`
- Phase 0 валидирует LLM-ответ перед записью в Jira (критические ошибки блокируют запись)
- Создание историй идемпотентно (манифест + проверка дублей)
- LLM API retry с экспоненциальным backoff на 429/502/503/504

## Цель

[Опиши здесь свою задачу]
