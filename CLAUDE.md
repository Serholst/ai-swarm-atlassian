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

@docs/ARCHITECTURE.md

## Code Style

- Line length: 100 (Black + Ruff)
- Type checking: MyPy strict mode
- Python 3.11+
- Data models: Pydantic v2 and dataclasses

## Context Efficiency Rules

- Use Grep with `head_limit: 20` unless you need exhaustive results
- Use Read with `limit` param for files >200 lines — read only the relevant section
- Delegate multi-file exploration (3+ files) to Task subagent (subagent_type=Explore)
- Never read files in `outputs/` directly — they are generated artifacts. Use limit/offset if needed
- Prefer Glob/Grep over Bash for file search — dedicated tools produce leaner output
- When running tests, use `pytest <specific_file>` not `pytest` on the full suite unless asked

## Hook-Driven Optimization Protocols

Six hooks in `.claude/hooks/` enforce token efficiency automatically:

**PreToolUse pipeline** (Read): `gate_read` → `diff_focus` → `exploration_guard`

- `gate_read`: blocks reads of `/outputs/` files >15KB
- `diff_focus`: for large files (>8KB), provides git diff or structural skeleton — use offset/limit to read only the relevant section
- `exploration_guard`: after 5+ distinct file reads, nudges toward Task subagents

**PostToolUse pipeline** (Read|Grep|Glob|Bash): `output_compressor` → `prompt_budget` → `context_monitor`

- `output_compressor`: flags repetitive patterns in tool output — use head_limit or narrower queries next
- `prompt_budget`: tiered mode shifts — yellow (40K chars): targeted reads, red (70K): subagent-only, critical (100K): /compact
- `context_monitor`: warns at 80K chars cumulative

**Behavioral rules enforced by hooks:**

- When `diff_focus` provides a skeleton, read only the line ranges you need — do not re-read the full file
- When `prompt_budget` enters red tier, delegate ALL exploration to Task subagents
- When `output_compressor` flags repetition, tighten your next query with head_limit or a narrower glob/pattern
- For tasks touching 3+ files, use multi-pass: scout with Task(model=haiku) → plan with Task(model=sonnet) → execute edits in main context
