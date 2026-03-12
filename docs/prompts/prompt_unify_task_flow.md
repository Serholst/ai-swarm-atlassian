# Prompt: Unify story creation into `--task` flow and remove `--create-stories`

## Overview

Currently `--create-stories` is a separate CLI command. This change integrates story creation into the `--task` auto-routing so the entire issue lifecycle is driven by a single command. The `--create-stories` flag is removed.

Read `docs/PROJECT_CONTEXT.md` and `CLAUDE.md` before starting.

---

## Current behavior

1. `execute.py --task PROJ-123` routes based on Jira status:
   - Backlog → Phase 0
   - AI To Do → full pipeline (Stages 1–5) → Post-Execution creates [PLAN REVIEW], transitions to "Human Plan Review"
   - Human Plan Review / other downstream statuses → `status_checker` (artifact validation)

2. `execute.py --create-stories PROJ-123` is a **separate** command that:
   - Checks [PLAN REVIEW] is Done
   - Creates child stories
   - Does NOT transition the parent issue

3. The transition "Human Plan Review" → "Ready for Dev" is not automated at all.

## Target behavior

`execute.py --task PROJ-123` handles the **entire lifecycle** including story creation:

```
Backlog                    → Phase 0
AI To Do                   → Full pipeline → Post-Execution → "Human Plan Review"
Human Plan Review          → Check [PLAN REVIEW] status:
                              - [PLAN REVIEW] is Done → create stories → transition to "Ready for Dev"
                              - [PLAN REVIEW] is NOT Done → inform user, exit 0
Ready for Dev and beyond   → status_checker (as today)
```

The `--create-stories` flag is **removed entirely**.

---

## Required Changes

### 1. Add "Human Plan Review" routing in execute.py status gate

In `execute.py`, the status gate (Stage 1.5, around lines 200–295) currently treats "Human Plan Review" as a generic downstream status and runs `status_checker`. Change this:

**Before:**
```
if status == "Backlog":
    → Phase 0
elif status in ("AI To Do", "AI-TO-DO"):
    → full pipeline
else:
    → status_checker
```

**After:**
```
if status == "Backlog":
    → Phase 0
elif status in ("AI To Do", "AI-TO-DO"):
    → full pipeline
elif status == "Human Plan Review":
    → human_plan_review_handler(mcp, issue_key, config, output_dir)
else:
    → status_checker
```

### 2. Implement `human_plan_review_handler()`

New function in `execute.py` (or a new module `src/executor/phases/plan_review_handler.py`):

```python
def human_plan_review_handler(
    mcp: MCPClientManager,
    issue_key: str,
    config: dict,
    output_dir: str = "outputs",
) -> int:
    """
    Handle --task on an issue in "Human Plan Review" status.

    1. Check if [PLAN REVIEW] task is Done
    2. If not Done → inform user, return 0
    3. If Done → create stories → transition to "Ready for Dev"
    """
```

**Step 1: Check [PLAN REVIEW] status**

Reuse `check_review_approved()` from `story_creator.py` (it already does exactly this — finds the linked [PLAN REVIEW] task and checks its status).

If NOT approved:
```
console.print(f"[yellow]⏳ [PLAN REVIEW] for {issue_key} is not yet Done.[/yellow]")
console.print(f"   Review task: {review_key}")
console.print(f"   Current status: {review_status}")
console.print(f"   Waiting for human approval. Re-run --task after review is complete.")
return 0
```

If no [PLAN REVIEW] found at all — this is an artifact gap. Log error and suggest re-running pipeline:
```
console.print(f"[red]✗ No [PLAN REVIEW] task found for {issue_key}.[/red]")
console.print(f"   This may indicate the pipeline didn't complete properly.")
console.print(f"   Consider re-running: python execute.py --task {issue_key} --force")
return 1
```

**Step 2: Create stories**

Call the existing story creation logic from `story_creator.py`. Specifically, reuse the core of `create_stories_pipeline()` — the part that:
- Extracts stories from the decomposition comment
- Runs duplicate detection
- Creates Jira issues with manifest tracking
- Returns `StoryCreationReport`

Handle the report:
- All created or skipped → proceed to transition
- Some failed → print report, return exit code 2 (do NOT transition — partial state)
- All failed → print report, return exit code 1

**Step 3: Transition to "Ready for Dev"**

After successful story creation (all created or skipped):
```python
target_status = config["jira"]["statuses"]["ready_for_dev"]  # "Ready for Dev"
mcp.jira_transition_issue(issue_key, target_status)

# Post-transition verification (existing pattern from jira_server.py)
current = mcp.jira_get_issue_status(issue_key)
if current.lower() != target_status.lower():
    logger.error(f"Transition verification failed: expected '{target_status}', got '{current}'")
    return 1

console.print(f"[green]✓ {issue_key} → {target_status}[/green]")
```

### 3. Remove `--create-stories` CLI flag

In `execute.py`:

- Remove `--create-stories` from argparse argument definitions
- Remove `create_stories_pipeline()` function (or keep as internal helper called by `human_plan_review_handler`)
- Remove the `elif args.create_stories:` branch in `main()`
- Update help text and docstring

### 4. Wrap with issue lock

The new handler must run inside `acquire_issue_lock()`, same as all other entry points:

```python
with acquire_issue_lock(issue_key):
    exit_code = human_plan_review_handler(mcp, issue_key, config, output_dir)
```

### 5. Update PROJECT_CONTEXT docs

In `docs/PROJECT_CONTEXT.md` and `docs/PROJECT_CONTEXT_RU.md`:

- Remove `--create-stories` from CLI entry points
- Update the status lifecycle diagram: "Human Plan Review" now transitions automatically via `--task` when [PLAN REVIEW] is Done
- Update the description: story creation is no longer a separate command

Updated lifecycle:
```
Backlog                    ← Phase 0 analyzes, asks questions
  ↓ (Phase 0.5: all BLOCKING questions resolved)
AI To Do                   ← Full pipeline (Stages 1–5) generates work plan
  ↓ (Post-Execution: pipeline succeeded, [PLAN REVIEW] created)
Human Plan Review          ← Human reviews plan, approves [PLAN REVIEW] → Done
  ↓ (--task: detects [PLAN REVIEW] Done, creates stories)
Ready for Dev              ← Child stories created, issue ready for development
  ↓
In Progress → Review → Deployment → Done
```

All transitions from Backlog through Ready for Dev are now driven by `--task`.

---

## Files to Modify

| File | Change |
|------|--------|
| `execute.py` | Add "Human Plan Review" branch in status gate, implement or call `human_plan_review_handler()`, remove `--create-stories` flag and its branch |
| `src/executor/phases/story_creator.py` | Extract core creation logic into a reusable function (if not already callable from outside `create_stories_pipeline`) |
| `docs/PROJECT_CONTEXT.md` | Update CLI section, lifecycle diagram, story creation description |
| `docs/PROJECT_CONTEXT_RU.md` | Same updates in Russian |

## Files NOT to modify

- `story_creator.py` core logic (duplicate detection, manifest, `StoryCreationReport`) — reuse as-is
- `check_review_approved()` — reuse as-is
- MCP servers — no changes needed
- Phase 0 / Phase 0.5 — unrelated

---

## Constraints

- The issue lock (`acquire_issue_lock`) must wrap the new handler
- All file writes must use `atomic_write()`
- Exit codes: 0 = success or waiting, 1 = error, 2 = partial story creation
- Follow project code style: Black (line-length 100), Ruff, type hints
- Add unit tests in `tests/unit/test_plan_review_handler.py`

## Tests

| Test | Assertion |
|------|-----------|
| `--task` on "Human Plan Review", [PLAN REVIEW] is Done | Stories created, issue transitioned to "Ready for Dev", exit 0 |
| `--task` on "Human Plan Review", [PLAN REVIEW] is NOT Done | Informational message, NO stories created, exit 0 |
| `--task` on "Human Plan Review", no [PLAN REVIEW] found | Error message, exit 1 |
| `--task` on "Human Plan Review", partial story failure | Report printed, NO transition, exit 2 |
| `--task` on "Human Plan Review", stories already exist (re-run) | All skipped, transition to "Ready for Dev", exit 0 |
| `--create-stories` flag used | Argparse error (flag removed) |
