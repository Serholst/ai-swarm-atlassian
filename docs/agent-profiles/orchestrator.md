# Agent Profile: Orchestrator

The Orchestrator is the primary agent that manages the full SDLC pipeline.

## Role

Owns end-to-end transformation of Jira issues into executable work plans.
Coordinates between Jira, Confluence, GitHub, and the LLM.

## Responsibilities

- Execute the 5-stage pipeline for AI-TO-DO issues
- Route Backlog issues to Phase 0
- Handle post-execution (decomposition, story creation, Jira transition)
- Monitor pipeline health and retry on failure

## Entry Point

```bash
python execute.py --task PROJ-123
# or via MCP:
run_pipeline(issue_key="PROJ-123")
```

## Decisions

- Routing: issue status determines which pipeline path to take
- Dry-run: use `--dry-run` to validate context without LLM cost
- Force: use `--force` to re-run even if artifacts exist

## Output

```json
{
  "trace_id": "abc123def456",
  "issue_key": "PROJ-123",
  "status": "success",
  "work_plan": "...",
  "stories": [...],
  "overall_confidence": 0.87,
  "output_files": { "plan": "outputs/PROJ-123/PROJ-123_work_plan.md" }
}
```

## Escalation

If pipeline fails (exit code 1), the Orchestrator:
1. Transitions the issue back to Backlog
2. Adds an error comment in Jira with the failure reason
3. Returns error details in JSON for the calling agent
