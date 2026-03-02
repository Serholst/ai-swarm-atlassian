# Prompt: Fix Critical Business Logic Gaps in AI-Swarm Pipeline

## Overview

Five critical gaps exist in the AI-Swarm pipeline that can lead to data corruption, duplicate Jira artifacts, and silent failures. This prompt describes each gap with exact locations, required changes, and testing criteria.

Read `docs/ARCHITECTURE.md` and `CLAUDE.md` before starting.

---

## Critical Gap 1: `--create-stories` creates duplicate child stories on re-run

### Problem

`create_jira_stories()` in `story_creator.py` (lines 346–414) has **zero duplicate detection**. Every run extracts stories from the decomposition comment and creates new Jira issues unconditionally. If the command is interrupted and re-run, or simply run twice — all child stories are duplicated.

Note: the review task (`decomposition.py`, lines 659–678) already has idempotency via `check_review_approved()`. Child story creation must match this pattern.

### Required Changes

#### 1.1 Add duplicate detection before story creation

In `story_creator.py`, before the creation loop (line 373), query existing child stories:

```
existing_children = mcp.jira_get_child_issues(parent_key)
existing_summaries = {child.summary for child in existing_children}
```

For each story in the loop, check if a child with matching summary prefix `[{layer}] {title}` already exists:
- If exact match found → skip creation, log: `"Story already exists: {existing_key}, skipping"`
- If partial match found (same title, different layer) → log warning, still create (layer may have been intentionally changed)
- Track skipped vs created counts in the result

#### 1.2 Add a creation manifest

After successful creation of each story, append to a manifest file `outputs/{ISSUE_KEY}/{KEY}_stories_manifest.json`:

```json
{
  "parent_key": "PROJ-123",
  "created_at": "2026-03-02T10:00:00Z",
  "stories": [
    {"order": 1, "jira_key": "PROJ-124", "summary": "[BE] Create API endpoint", "status": "created"},
    {"order": 2, "jira_key": null, "summary": "[FE] Build form component", "status": "failed", "error": "API timeout"}
  ]
}
```

On re-run, load the manifest first:
- Stories with `status: "created"` and valid `jira_key` → skip (verify key still exists in Jira)
- Stories with `status: "failed"` → retry creation
- Stories not in manifest → create as new

This gives resume-on-failure semantics.

### Files to Modify

| File | Change |
|------|--------|
| `src/executor/phases/story_creator.py` | Add `_load_manifest()`, `_save_manifest()`, duplicate check in creation loop |
| `src/executor/mcp/client.py` | Add `jira_get_child_issues(parent_key)` if not present (check existing methods first) |

### Tests (`tests/unit/test_story_creator_idempotency.py`)

- Create stories → re-run → assert no duplicates, same Jira keys returned
- Partial failure (3 of 5 created) → re-run → only 2 missing stories created
- Manifest with stale key (deleted in Jira) → re-run → re-creates that story
- Completely fresh run (no manifest) → all stories created normally

---

## Critical Gap 2: Partial failure during story creation — no rollback or resume

### Problem

In `story_creator.py` (lines 410–414), if story creation fails mid-loop:
- Successfully created stories stay in Jira (no rollback)
- Failed stories are lost — `created` list only contains successes
- Dependency links (`create_dependency_links`, lines 417–464) are created only for successful stories
- Re-run without Gap 1 fix creates duplicates; WITH Gap 1 fix, this gap is partially addressed by the manifest

### Required Changes

#### 2.1 Track all creation outcomes (not just successes)

Change the creation loop to return a `StoryCreationReport`:

```python
@dataclass
class StoryCreationOutcome:
    story: DecomposedStory
    jira_key: Optional[str]  # None if failed
    status: Literal["created", "skipped", "failed"]
    error: Optional[str]

@dataclass
class StoryCreationReport:
    outcomes: list[StoryCreationOutcome]

    @property
    def created(self) -> list[tuple[DecomposedStory, str]]:
        return [(o.story, o.jira_key) for o in self.outcomes if o.status == "created"]

    @property
    def failed(self) -> list[StoryCreationOutcome]:
        return [o for o in self.outcomes if o.status == "failed"]

    @property
    def skipped(self) -> list[StoryCreationOutcome]:
        return [o for o in self.outcomes if o.status == "skipped"]
```

#### 2.2 Report partial failures to user

In `execute.py` `create_stories_pipeline()` (lines 763–768), after creation:

- If `report.failed` is non-empty, print clear summary:
  ```
  ⚠ Created 3/5 stories. 2 failed:
    - [FE] Build form component: API timeout
    - [QA] Write integration tests: 403 Forbidden
  Re-run `--create-stories PROJ-123` to retry failed stories.
  ```
- Return exit code 2 (partial success) instead of 0

#### 2.3 Dependency links — only for created stories, with clear warnings

Current behavior (creating links only for existing stories) is acceptable, but add explicit warnings:

```
⚠ Dependency link skipped: [FE] → [BE] — [BE] story creation failed
```

### Files to Modify

| File | Change |
|------|--------|
| `src/executor/phases/story_creator.py` | New `StoryCreationReport` dataclass, refactor `create_jira_stories()` return type |
| `execute.py` | Handle partial success in `create_stories_pipeline()`, exit code 2 |

### Tests (`tests/unit/test_story_creation_report.py`)

- All stories succeed → exit code 0, all in `report.created`
- Some fail → exit code 2, failed listed with errors
- All fail → exit code 1
- Dependencies partially created → warnings logged for missing targets

---

## Critical Gap 3: Phase 0 writes invalid LLM response to Jira without blocking

### Problem

In `phase_zero.py` (lines 764–792), `validate_phase_zero()` returns a list of errors (missing XML sections, empty use cases, etc.), but the result is **logged as warning and execution continues**. The invalid response is written to the Jira description and clarification questions are posted — even if the LLM returned garbage.

Stage 5 (full pipeline) has retry logic with regex pre-processing. Phase 0 has none.

### Required Changes

#### 3.1 Add retry logic to Phase 0 LLM calls

Implement retry for Phase 0 following the existing Stage 5 pattern (`llm_executor.py` lines 192–295):

```python
MAX_PHASE0_RETRIES = 1  # 2 total attempts (lower than Stage 5 because Phase 0 is simpler)

for attempt in range(1, MAX_PHASE0_RETRIES + 2):
    response = call_phase_zero_llm(prompt)
    validation_errors = validate_phase_zero(response)

    if not validation_errors:
        break  # Valid response

    if attempt <= MAX_PHASE0_RETRIES:
        logger.warning(f"Phase 0 attempt {attempt} validation failed: {validation_errors}. Retrying...")
        prompt = build_phase_zero_retry_prompt(validation_errors, response)
    else:
        logger.error(f"Phase 0 max retries reached. Validation errors: {validation_errors}")
```

#### 3.2 Block Jira write on critical validation failures

Classify validation errors into **blocking** and **non-blocking**:

**Blocking** (do NOT write to Jira):
- Missing `<use_case>` section entirely
- Missing `<definition_of_ready>` section entirely
- Empty response / unparseable XML

**Non-blocking** (write to Jira with warning):
- Missing `<chain_of_thought>` (internal reasoning, not user-facing)
- Missing `<complexity_estimate>` (nice-to-have)
- Incomplete sub-elements within valid sections

After max retries, if blocking errors remain:
- Do NOT update Jira description
- Do NOT post clarification comment
- Save the raw response to `outputs/{KEY}/{KEY}_phase0_failed.md` for debugging
- Print: `"✗ Phase 0 failed: LLM response missing critical sections. Raw output saved to {path}. Jira was NOT modified."`
- Return exit code 1

#### 3.3 Apply same logic to Phase 0.5

Phase 0.5 (`execute_phase_zero_feedback()`, line 956) has the same gap. Apply identical retry + blocking validation.

### Files to Modify

| File | Change |
|------|--------|
| `src/executor/phases/phase_zero.py` | Add retry loop, classify errors as blocking/non-blocking, gate Jira writes |
| `src/executor/prompts/phase_zero_prompt.py` | Add `PHASE0_RETRY_PROMPT_TEMPLATE` |
| `src/executor/prompts/phase_zero_feedback_prompt.py` | Add `PHASE05_RETRY_PROMPT_TEMPLATE` |
| `src/executor/phases/validation.py` | Optional: move Phase 0 validation here for consistency |

### Tests (`tests/unit/test_phase_zero_validation_gate.py`)

- Valid Phase 0 response → written to Jira, exit 0
- Missing `<use_case>` → retry once → still missing → NOT written to Jira, exit 1
- Missing `<chain_of_thought>` only → written to Jira with warning, exit 0
- Retry succeeds on second attempt → written to Jira, exit 0
- Phase 0.5 same behavior

---

## Critical Gap 4: DeepSeek API failure crashes pipeline without retry

### Problem

In `llm_executor.py` `_call_llm()` (lines 505–540), API call failures (network timeout, 429 rate limit, 503 service unavailable) raise an exception immediately. The retry loop (lines 192–295) only handles **validation failures**, not **API failures**.

Contrast with `jira_server.py` (lines 391–396) which already implements exponential backoff for 429/503 on Jira API calls.

### Required Changes

#### 4.1 Add retry with exponential backoff for transient API errors

Wrap the API call in `_call_llm()` with retry logic:

```python
TRANSIENT_ERRORS = {429, 502, 503, 504}
MAX_API_RETRIES = 3
BASE_BACKOFF_SECONDS = 2

for api_attempt in range(1, MAX_API_RETRIES + 1):
    try:
        response = self.client.chat.completions.create(...)
        return response  # Success
    except Exception as e:
        status_code = getattr(e, 'status_code', None)
        if status_code in TRANSIENT_ERRORS and api_attempt < MAX_API_RETRIES:
            wait = BASE_BACKOFF_SECONDS * (2 ** (api_attempt - 1))  # 2s, 4s, 8s
            logger.warning(f"DeepSeek API error {status_code}, retry {api_attempt}/{MAX_API_RETRIES} in {wait}s")
            time.sleep(wait)
            continue
        raise  # Non-transient or max retries exhausted
```

Apply same pattern to `_call_llm_retry()` (line 570+).

#### 4.2 Add connection timeout configuration

Add to `config/sdlc_config.yaml` under `llm`:

```yaml
llm:
  api_timeout_seconds: 120
  max_api_retries: 3
  backoff_base_seconds: 2
```

Pass `timeout=config.llm.api_timeout_seconds` to the API client.

#### 4.3 Structured error reporting on final failure

If all retries exhausted, instead of raw exception, produce a clear message:

```
✗ DeepSeek API unavailable after 3 attempts (last error: 503 Service Unavailable).
  Pipeline cannot proceed without LLM. Check API status at https://status.deepseek.com
  Context was saved — you can retry with: python execute.py --refine PROJ-123
```

Save the execution context before raising so `--refine` can resume without re-fetching all MCP data.

### Files to Modify

| File | Change |
|------|--------|
| `src/executor/phases/llm_executor.py` | Add retry loop in `_call_llm()` and `_call_llm_retry()`, timeout config |
| `config/sdlc_config.example.yaml` | Add `api_timeout_seconds`, `max_api_retries`, `backoff_base_seconds` |
| `src/executor/phases/phase_zero.py` | Same retry pattern for Phase 0 LLM calls (if using separate client) |

### Tests (`tests/unit/test_llm_api_retry.py`)

- Mock 429 once → retry succeeds → pipeline continues
- Mock 503 three times → final failure → clear error message, context saved
- Mock connection timeout → retry with backoff
- Mock 400 (bad request) → no retry, immediate failure (not transient)
- Verify backoff timing: 2s, 4s, 8s

---

## Critical Gap 5: Concurrent execution on same issue — race conditions

### Problem

No locking mechanism exists anywhere in the project. Two simultaneous `execute.py --task PROJ-123` runs will:

1. **Overwrite each other's output files** — `context_store.py:64`, `llm_executor.py:360–486` use raw `write_text()` without atomic writes or locks
2. **Race on Jira status transitions** — `jira_server.py:446–478` fetches available transitions, then POSTs — if status changed in between, the POST fails with 400
3. **Corrupt context_store.json** — one process reads while another writes partial JSON
4. **Double-post Jira comments** — both processes post the same analysis/plan comment

### Required Changes

#### 5.1 Add file-based lock per issue key

Create a lock file mechanism in a new module `src/executor/utils/issue_lock.py`:

```python
import fcntl
from pathlib import Path
from contextlib import contextmanager

LOCK_DIR = Path("outputs/.locks")

@contextmanager
def acquire_issue_lock(issue_key: str, timeout: int = 10):
    """
    File-based lock per issue key.
    Raises IssueLockError if another process holds the lock.
    """
    LOCK_DIR.mkdir(parents=True, exist_ok=True)
    lock_path = LOCK_DIR / f"{issue_key}.lock"

    lock_file = open(lock_path, "w")
    try:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        lock_file.close()
        raise IssueLockError(
            f"Another pipeline is already running for {issue_key}. "
            f"Wait for it to finish or remove {lock_path} if stale."
        )

    try:
        lock_file.write(f"pid={os.getpid()}\nstarted={datetime.now().isoformat()}\n")
        lock_file.flush()
        yield
    finally:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
        lock_file.close()
        lock_path.unlink(missing_ok=True)
```

#### 5.2 Wrap pipeline entry points with lock

In `execute.py`, wrap each pipeline entry point:

```python
# Full pipeline
with acquire_issue_lock(issue_key):
    exit_code = execute_pipeline(mcp, issue_key, config, ...)

# Phase 0
with acquire_issue_lock(issue_key):
    exit_code = execute_phase_zero(mcp, issue_key, config, ...)

# Create stories
with acquire_issue_lock(issue_key):
    exit_code = create_stories_pipeline(mcp, issue_key, config, ...)
```

#### 5.3 Atomic file writes

Replace all `filepath.write_text(content)` with atomic write helper:

```python
def atomic_write(filepath: Path, content: str) -> None:
    """Write to temp file, then rename — prevents partial writes."""
    tmp = filepath.with_suffix(filepath.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    tmp.replace(filepath)  # Atomic on POSIX
```

Apply in:
- `context_store.py:64` — context store serialization
- `llm_executor.py:360, 464, 475, 486` — output files
- `phase_zero.py:487, 522, 713, 904, 1034, 1081` — Phase 0 outputs

#### 5.4 Jira transition with conflict detection

In `jira_server.py` `transition_issue()` (line 446), add post-transition verification:

```python
def transition_issue(self, issue_key: str, target_status: str) -> bool:
    # ... existing: fetch transitions, find matching, POST ...

    # After POST: verify transition actually happened
    current_status = self.get_issue_status(issue_key)
    if current_status.lower() != target_status.lower():
        raise TransitionConflictError(
            f"Transition failed: expected '{target_status}', "
            f"but issue is in '{current_status}'. "
            f"Another process may have modified the issue."
        )
    return True
```

### Files to Modify

| File | Change |
|------|--------|
| `src/executor/utils/issue_lock.py` | New file: `acquire_issue_lock()`, `IssueLockError` |
| `src/executor/utils/file_utils.py` | New file (or extend existing): `atomic_write()` |
| `execute.py` | Wrap all entry points with `acquire_issue_lock()` |
| `src/executor/phases/context_store.py` | Use `atomic_write()` |
| `src/executor/phases/llm_executor.py` | Use `atomic_write()` |
| `src/executor/phases/phase_zero.py` | Use `atomic_write()` |
| `src/executor/mcp/servers/jira_server.py` | Post-transition verification |

### Tests (`tests/unit/test_issue_lock.py`, `tests/unit/test_atomic_write.py`)

**Locking:**
- Acquire lock → second acquire on same key → `IssueLockError`
- Acquire lock → release → second acquire succeeds
- Lock file contains PID and timestamp
- Stale lock file (process dead) — document manual cleanup

**Atomic writes:**
- Write succeeds → file contains full content
- Simulated crash (delete temp before rename) → original file unchanged
- Concurrent writes to different issue keys → no interference

**Jira transition:**
- Transition succeeds → verify returns True
- Status changed by another process → `TransitionConflictError` raised

---

## Implementation Order

Execute in this order due to dependencies:

1. **Gap 5** (locking + atomic writes) — foundational safety, other fixes assume safe writes
2. **Gap 4** (API retry) — unblocks reliable pipeline execution
3. **Gap 3** (Phase 0 validation gate) — prevents bad data in Jira
4. **Gap 1** (story duplicate detection) — requires manifest file (depends on atomic writes from Gap 5)
5. **Gap 2** (creation report + partial failure) — builds on Gap 1 manifest

## Constraints

- Do NOT break existing behavior for happy-path scenarios
- All new code: Black (line-length 100), Ruff, type hints, MyPy strict
- New dataclasses: follow existing patterns (Pydantic v2 or `@dataclass` as used in each file)
- Unit tests for each gap in `tests/unit/`
- Config changes must be backward-compatible (new fields with defaults)
