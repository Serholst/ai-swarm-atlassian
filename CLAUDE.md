# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

AI-Swarm is a multi-agent SDLC orchestrator that transforms Jira issues into detailed work plans through a 5-stage pipeline. It integrates with Atlassian (Jira/Confluence) and GitHub via MCP servers, using DeepSeek LLM for work plan generation.

## Common Commands

```bash
# Setup
python3 -m venv venv && source venv/bin/activate && pip install -r requirements.txt
cp .env.example .env
cp config/sdlc_config.example.yaml config/sdlc_config.yaml

# Run pipeline (full execution: AI-TO-DO → work plan)
# Note: --task auto-routes based on Jira status — Backlog issues go to Phase 0
python execute.py --task PROJ-123
python execute.py --task PROJ-123 --dry-run    # skip LLM call
python execute.py --task PROJ-123 --output-dir ./my_outputs

# Phase 0 (Backlog Analysis → requirements + DoR)
python execute.py --phase0 PROJ-123
# Re-running --phase0 auto-detects Phase 0.5 if assignee feedback exists

# Refinement (re-run Stage 5 with human feedback, no MCP needed)
python execute.py --refine PROJ-123 --feedback "Split step 3 into BE and FE"

# Story creation (from approved decomposition)
python execute.py --create-stories PROJ-123

# Tests
pytest tests/unit/                          # unit tests only
pytest tests/unit/test_validation.py        # single test file
./run_tests.sh                              # MCP integration tests (requires .env credentials)
python tests/test_mcp_integration.py        # MCP integration tests directly

# Code quality
black --line-length 100 src/
ruff check --line-length 100 src/
mypy src/
```

## Architecture

### 5-Stage Pipeline (Full Execution: AI-TO-DO → Work Plan)

```
CLI Input → Stage 1 (Trigger) → Stage 2 (Jira Enrichment) → Stage 3a/3b/3c (Confluence + GitHub + Templates) → Stage 4 (Aggregate) → Stage 5 (LLM Execute)
```

- **Stage 1 - Trigger**: Parses and validates issue key from CLI input
- **Stage 1.5 - Status Gate**: Fetches issue status; Backlog → auto-routes to Phase 0, "AI To Do" → continues full pipeline, other statuses → proceeds with warning
- **Stage 2 - Jira Enrichment**: Fetches issue details into `JiraContext` (summary, description, fields, comments, assignee accountId)
- **Stage 3a - Confluence Knowledge**: Two-Stage Retrieval — fetches mandatory core documents (Project Passport, Logical Architecture), then LLM-filters supporting documents via DeepSeek
- **Stage 3b - GitHub Context**: Fetches repo structure, configs, recent commits, open PRs into `GitHubContext`
- **Stage 3c - Template Compliance**: Retrieves Confluence templates from "Templates/Patterns" folder; injected into prompts to enforce document structure
- **Stage 4 - Data Aggregation**: Unifies all contexts into `ExecutionContext` (including templates)
- **Stage 5 - LLM Execution**: Constructs prompt, calls DeepSeek, validates response, extracts work plan and story decomposition

### Phase 0 Pipeline (Backlog → AI-TO-DO)

```
--phase0 → Context Build → Phase 0 Analysis (Use Cases + DoR) → Write to Jira Description (ADF)
         ↳ Auto-detect Phase 0.5 → Re-evaluate with Assignee Feedback → Update Jira → Transition if DoR met
```

- **Phase 0**: Generates Use Cases and Definition of Ready (DoR) with clarification questions. Writes structured XML analysis into the Jira description via ADF expand blocks.
- **Phase 0.5 (Feedback Incorporation)**: Auto-triggered on subsequent `--phase0` runs when the system detects (1) an existing Phase 0 analysis in the description, and (2) new comments from the assignee. Filters comments by `assignee_account_id` to ignore non-assignee input. Re-evaluates DoR — if all BLOCKING questions are resolved, auto-transitions the issue to "AI To Do".

**Phase 0.5 auto-detection logic** (in `execute_phase_zero()`):

1. Check if Jira description contains "Phase 0: Requirements Analysis" expand block
2. Extract assignee feedback (comments after Phase 0 timestamp, from assignee only)
3. If both conditions met → route to `execute_phase_zero_feedback()`
4. Loads previous analysis from `_phase05.md` (preferred) or `_phase0.md` (fallback)

### Key Modules

| Module | Purpose |
|--------|---------|
| `src/executor/phases/context_builder.py` | Stages 1-4: all context gathering and enrichment |
| `src/executor/phases/llm_executor.py` | Stage 5: LLM API calls, response validation, retries |
| `src/executor/phases/decomposition.py` | Story extraction from LLM response with layer taxonomy |
| `src/executor/phases/validation.py` | Work plan validation rules (structure, dependencies, quality) |
| `src/executor/phases/phase_zero.py` | Phase 0 (Backlog Analysis) and Phase 0.5 (Feedback Incorporation) |
| `src/executor/phases/story_creator.py` | Auto-create Jira child stories from decomposition |
| `src/executor/phases/context_store.py` | Serialize/deserialize ExecutionContext for `--refine` mode |
| `src/executor/mcp/client.py` | MCP client manager — lifecycle for Jira, Confluence, GitHub servers |
| `src/executor/mcp/servers/jira_server.py` | Custom Jira MCP server (ADF→Markdown conversion) |
| `src/executor/mcp/servers/confluence_server.py` | Custom Confluence MCP server |
| `src/executor/models/execution_context.py` | Core dataclasses: `ExecutionContext`, `JiraContext`, `GitHubContext`, `RefinedConfluenceContext` |
| `src/executor/prompts/system_prompt.py` | System prompt construction for DeepSeek |
| `src/executor/prompts/user_prompt.py` | User prompt construction with context injection |
| `src/executor/prompts/phase_zero_prompt.py` | Phase 0 prompt templates (Backlog Analysis) |
| `src/executor/prompts/phase_zero_feedback_prompt.py` | Phase 0.5 prompt templates (Feedback Incorporation) |
| `src/executor/prompts/template_compliance.py` | Shared helper for template compliance prompt sections |
| `execute.py` | CLI entry point (argparse-based) |

### MCP Integration

Three MCP servers managed by `MCPClientManager`:
- **Jira** — custom Python server (`jira_server.py`)
- **Confluence** — custom Python server (`confluence_server.py`)
- **GitHub** — official `@modelcontextprotocol/server-github` via npx

### Data Flow

`JiraContext` + `RefinedConfluenceContext` + `GitHubContext` → `ExecutionContext` → LLM prompt → `DecompositionResult` (stories with layer tags: BE/FE/INFRA/DB/QA/DOCS/GEN)

### Output Files

Pipeline generates files in `outputs/{ISSUE_KEY}/`:

- Full pipeline: context, selection log, prompt, LLM reasoning, work plan, and metrics
- Phase 0: `{KEY}_phase0.md` — initial analysis with Use Cases and DoR
- Phase 0.5: `{KEY}_phase05.md` — updated analysis after feedback incorporation
- Context store: `{KEY}_context_store.json` — serialized ExecutionContext for `--refine` mode

## Configuration

- **Environment**: `.env` — Atlassian credentials, DeepSeek API key, GitHub token
- **Workflow config**: `config/sdlc_config.yaml` — Confluence structure, Jira workflow statuses, layer taxonomy, LLM parameters (model: `deepseek-chat`, temperature: 0.2)
- **Custom Jira fields**: configured via `JIRA_PROJECT_DROPDOWN_FIELD`, `JIRA_PROJECT_TEXT_FIELD`, `JIRA_PROJECT_LINK_FIELD` env vars

## Code Style

- Line length: 100 (Black + Ruff)
- Type checking: MyPy strict mode
- Python 3.11+
- Data models: Pydantic v2 and dataclasses
