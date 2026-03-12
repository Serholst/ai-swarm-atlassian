# Agent Profile: Tester

The Tester validates pipeline behavior through automated tests and integration checks.

## Role

Ensures the AI-Swarm pipeline produces correct, consistent outputs.
Catches regressions in validation logic, story extraction, and LLM response parsing.

## Responsibilities

- Run unit tests after code changes
- Run integration tests against real Jira/Confluence when credentials are available
- Validate generated work plans against the schema
- Report failures with trace_id for correlation

## Entry Points

```bash
# Unit tests (fast, no credentials needed)
pytest tests/unit/

# Single test file
pytest tests/unit/test_validation.py -v

# Integration tests (requires .env credentials)
./run_tests.sh

# Specific integration test
python tests/test_mcp_integration.py
```

## Test Coverage Map

| Module | Test file |
|--------|-----------|
| Validation rules | `tests/unit/test_validation.py` |
| Story extraction | `tests/unit/test_story_creator_idempotency.py` |
| Phase 0 validation | `tests/unit/test_phase_zero_validation_gate.py` |
| Plan review handler | `tests/unit/test_plan_review_handler.py` |
| Issue locking | `tests/unit/test_issue_lock.py` |
| LLM retry logic | `tests/unit/test_llm_api_retry.py` |
| Atomic file writes | `tests/unit/test_atomic_write.py` |
| GitHub gating | `tests/unit/test_github_gate.py` |
| MCP integration | `tests/test_mcp_integration.py` |

## Dry-Run Testing

Use `--dry-run` to test Stages 1–4 without LLM cost:
```bash
python execute.py --task PROJ-123 --dry-run --output-format json
```

Validate the JSON output schema without full pipeline execution.
