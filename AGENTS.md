# AGENTS.md

Universal AI agent instructions for AI-Swarm.
This file is read by Claude Code, GitHub Copilot, Cursor, Gemini CLI, and any other AI agent
working with this repository. Rules here override agent defaults.

---

## What this project does

AI-Swarm is a multi-agent SDLC orchestrator that transforms Jira issues into detailed work plans
via a 5-stage pipeline. It integrates with Atlassian (Jira/Confluence) and GitHub via MCP servers,
using DeepSeek LLM for plan generation.

---

## Quick Start

```bash
python3 -m venv venv && source venv/bin/activate && pip install -r requirements.txt
cp .env.example .env && cp config/sdlc_config.example.yaml config/sdlc_config.yaml
python execute.py --task PROJ-123               # Full pipeline
python execute.py --task PROJ-123 --dry-run     # Skip LLM call (fast)
python execute.py --task PROJ-123 --output-format json   # Machine-readable output
python execute.py --phase0 PROJ-123             # Phase 0: Backlog Analysis
python execute.py --refine PROJ-123 --feedback "Split step 3"  # Refine with feedback
```

---

## Available Interfaces

| Interface | How to use |
|-----------|-----------|
| **CLI** | `python execute.py --task PROJ-123` |
| **MCP Server** | `python src/executor/mcp/pipeline_server.py` — exposes pipeline as MCP tools |
| **JSON output** | `--output-format json` → structured JSON to stdout |
| **Python API** | Import from `src/executor/` (see Architecture below) |

### MCP Server Tools (for agent-to-agent use)

Start the server: `python src/executor/mcp/pipeline_server.py`

| Tool | Input | Description |
|------|-------|-------------|
| `run_pipeline` | `issue_key`, `dry_run?` | Full 5-stage pipeline |
| `run_phase0` | `issue_key`, `dry_run?` | Phase 0: Backlog Analysis |
| `refine_plan` | `issue_key`, `feedback` | Refine plan with feedback |

---

## Architecture

### 5-Stage Pipeline

```
CLI → Stage 1 (Trigger) → Stage 2 (Jira) → Stage 3 (Confluence+GitHub+Templates)
    → Stage 4 (Aggregate) → Stage 5 (DeepSeek LLM) → Work Plan
```

**Key modules:**
- `src/executor/phases/context_builder.py` — Stages 1–4
- `src/executor/phases/llm_executor.py` — Stage 5: LLM call
- `src/executor/phases/phase_zero.py` — Phase 0 and 0.5
- `src/executor/mcp/client.py` — MCP client manager
- `execute.py` — CLI entry point

**Output files** in `outputs/{ISSUE_KEY}/`:
- `{KEY}_work_plan.md` — generated work plan
- `{KEY}_pipeline_metrics.json` — structured metrics
- `{KEY}_context_store.json` — serialized context for refinement

---

## Coding Rules

- Python 3.11+, line length 100, Black + Ruff formatting, MyPy strict
- Data models: Pydantic v2 and dataclasses
- **Never read files in `outputs/`** — they are generated artifacts
- Use `pytest tests/unit/` for unit tests; `./run_tests.sh` for integration tests

---

## Token/Context Efficiency Rules

- Use `head_limit: 20` in Grep unless exhaustive results needed
- Use `limit` param in Read for files >200 lines
- Delegate multi-file exploration (3+ files) to Task subagents
- For tasks touching 3+ files: scout (haiku) → plan (sonnet) → edit (main context)

---

## Environment Variables

Required in `.env`:

```
ATLASSIAN_URL=https://yourorg.atlassian.net
ATLASSIAN_EMAIL=bot@yourorg.com
ATLASSIAN_API_TOKEN=...
DEEPSEEK_API_KEY=...
GITHUB_TOKEN=...
JIRA_PROJECT_KEY=PROJ
```

---

## Routing Logic (Stage 1.5)

The pipeline auto-routes based on Jira issue status:

| Status | Route |
|--------|-------|
| `Backlog` | → Phase 0 (Requirements Analysis) |
| `AI To Do` + all artifacts exist | → Pre-flight checklist (bypass with `--force`) |
| `AI To Do` | → Full 5-stage pipeline |
| Any other | → Proceeds with warning |

---

## Do Not

- Do not commit files from `outputs/`, `venv/`, or `__pycache__/`
- Do not run `pytest` on the full suite without explicit request — use `pytest tests/unit/`
- Do not modify `outputs/` files — they are generated, re-run the pipeline instead
- Do not skip hooks with `--no-verify`
