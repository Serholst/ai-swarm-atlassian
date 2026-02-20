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
