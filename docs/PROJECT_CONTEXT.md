# AI-Swarm — Project Context

Use this file as reusable context when working with the AI-Swarm codebase. Paste it into a new dialogue and add your goal.

---

## What This Project Does

AI-Swarm is a CLI tool that automates the preparation of development tasks. It takes a Jira issue from a vague backlog idea and transforms it into a structured, LLM-generated work plan with concrete child stories — ready for a development team to pick up.

The system integrates three external platforms via MCP (Model Context Protocol) servers: Jira, Confluence, and GitHub. It uses the DeepSeek LLM (via OpenAI-compatible API) for analysis and plan generation.

---

## Business Process (End to End)

### Phase 0 — Backlog Analysis (Jira status: Backlog)

Input: a raw Jira issue with minimal description.

The system:
1. Gathers context from Jira fields and Confluence project documentation
2. Sends context to LLM, which produces: feature type classification (`new_feature`, `update_existing`, `documentation_only`, `process`), use cases, work areas by layer, Definition of Ready (DoR) with BLOCKING/NON-BLOCKING questions
3. Validates the LLM response — if critical sections (use_case, definition_of_ready) are missing, retries once; if still invalid, does NOT write to Jira and exits with error
4. Writes the analysis into the Jira description as ADF expand blocks
5. Posts clarification questions as a Jira comment for the assignee

### Phase 0.5 — Feedback Incorporation (auto-detected)

Triggered automatically on subsequent `--phase0` runs when the system detects:
- An existing Phase 0 analysis in the description
- New comments from the assignee (filtered by `assignee_account_id` — non-assignee comments are ignored)

The system re-evaluates the DoR. If all BLOCKING questions are resolved, it auto-transitions the issue to "AI To Do".

### Full Pipeline — Work Plan Generation (Jira status: AI To Do)

Five-stage pipeline:

**Stage 1 — Trigger:** Parse and validate issue key from CLI input.

**Stage 1.5 — Status Gate:** Fetch issue status and route:
- Backlog → redirect to Phase 0
- AI To Do → continue full pipeline (or run pre-flight checklist if artifacts already exist)
- Other statuses → run status checklist (validate per-status requirements)

**Stage 2 — Jira Enrichment:** Fetch issue details into `JiraContext`: summary, description, fields, comments, labels, components, assignee, parent/subtask hierarchy.

**Stage 3a — Confluence Knowledge (Two-Stage Retrieval):**
1. Fetch mandatory core documents: Project Passport, Logical Architecture
2. Discover supporting documents via CQL search, then LLM-filter (rerank) them for relevance to the task
3. Retrieve Confluence templates from "Templates/Patterns" folder for document structure enforcement

**Stage 3b — GitHub Context (conditional):**
A gate function `should_fetch_github_context()` evaluates whether to fetch:
- If the task is not code-related (labels `no-code`/`docs-only`/`process`, or feature_type `documentation_only`/`process`) → skip
- Searches for a GitHub repo URL across sources in priority order: Jira description → assignee comments → custom Jira field (`project_link`) → Confluence Passport
- If no URL found anywhere → skip
- If the task-level URL differs from the project-level URL (Confluence Passport) → use task-level URL and log the override
- When fetching: retrieves repo structure, config files, code snippets, recent commits, open PRs
- Deduplicates against Confluence: if topics (tech_stack, architecture, api_contracts, database, deployment) are already documented, skips those GitHub fetches

**Stage 4 — Data Aggregation:** Unify all contexts into `ExecutionContext` — the single object passed to LLM.

**Stage 5 — LLM Execution:** Construct prompt from `ExecutionContext`, call DeepSeek with retry logic (exponential backoff on 429/502/503/504), validate response structure, extract work plan and story decomposition. Validation retry loop (up to 2 attempts) fixes malformed responses via targeted re-prompting.

**Post-Execution (automatic after Stage 5):**
On successful pipeline completion (`post_execution.py`):
1. Analysis & Decomposition runs — parses LLM response, extracts story decomposition
2. Creates a blocking task `[PLAN REVIEW] {KEY} Approve Architecture (HUMAN)` with a Blocks link to the parent issue (idempotent — skips if already exists)
3. Posts a consolidated ADF comment with blocks: Context Summary, Technical Decomposition, Executor Rationale, Clarification Questions
4. Automatically transitions the issue from "AI To Do" → "Human Plan Review"

On failure: issue transitions back to "Backlog" with an error explanation comment.

### Issue Lifecycle by Status

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

Transitions are performed automatically by the system at: Phase 0.5 → "AI To Do", Post-Execution → "Human Plan Review", and Plan Review handler → "Ready for Dev" (when [PLAN REVIEW] is Done). All transitions from Backlog through Ready for Dev are driven by `--task`. Remaining transitions (In Progress → Done) are managed manually by the team.

When `--task` is run on an issue in "Human Plan Review", the system checks whether the [PLAN REVIEW] task is Done. If approved, it creates child stories and transitions to "Ready for Dev". If not yet approved, it informs the user and exits. For statuses past "Ready for Dev", `status_checker` runs — validates required artifacts for the current status and auto-fixes missing ones.

### Story Creation (automatic via --task on "Human Plan Review")

Triggered automatically when `--task` is run on an issue in "Human Plan Review" status and the [PLAN REVIEW] task is Done.

Process:
1. Verify [PLAN REVIEW] is approved (Done) — if not, inform user and exit
2. Re-extract stories from the Technical Decomposition comment
3. Check for existing child stories to prevent duplicates (idempotent)
4. Create Jira Story issues linked to the parent Feature with correct hierarchy
5. Each story is tagged with a layer: BE, FE, INFRA, DB, QA, DOCS, GEN
6. A creation manifest tracks outcomes — if creation is interrupted, re-run resumes from where it stopped
7. Partial failures (some stories created, some failed) produce exit code 2 with a clear report — no transition occurs
8. On full success, the parent issue is transitioned to "Ready for Dev"

### Refinement

Re-run Stage 5 with human feedback text, without re-fetching MCP data:
```
python execute.py --refine PROJ-123 --feedback "Split step 3 into BE and FE"
```
Uses serialized `ExecutionContext` from `context_store.json`.

---

## Safety Mechanisms

**Issue Locking:** File-based lock per issue key (`outputs/.locks/{KEY}.lock`) prevents concurrent pipeline runs on the same issue. Uses `fcntl.LOCK_EX | LOCK_NB` — fails immediately if another process holds the lock.

**Atomic File Writes:** All output files are written via `atomic_write()` — write to `.tmp`, then `Path.replace()` (atomic on POSIX). Prevents partial writes on crash or concurrent access.

**LLM API Retry:** Transient errors (429, 502, 503, 504) trigger exponential backoff: 2s → 4s → 8s, up to 3 attempts. Non-transient errors fail immediately. Configurable via `sdlc_config.yaml`.

**Phase 0 Validation Gate:** LLM responses are validated before writing to Jira. Critical errors (missing use_case or definition_of_ready sections entirely) block the Jira write — the raw response is saved to `outputs/` for debugging, but Jira is not modified. Non-critical errors (missing chain_of_thought) produce warnings but allow the write.

**Story Creation Idempotency:** Duplicate detection by matching child story summaries. A manifest file (`_stories_manifest.json`) tracks creation outcomes for resume-on-failure.

**Jira Transition Verification:** After POSTing a status transition, the system verifies the issue actually reached the target status, catching race conditions from concurrent modifications.

---

## CLI Entry Points

```bash
# Full pipeline (auto-routes based on Jira status)
python execute.py --task PROJ-123
python execute.py --task PROJ-123 --dry-run      # skip LLM call
python execute.py --task PROJ-123 --force         # bypass pre-flight checks
python execute.py --task PROJ-123 --output-dir ./out

# Phase 0 (Backlog → requirements + DoR)
python execute.py --phase0 PROJ-123
# Re-running auto-detects Phase 0.5 if assignee feedback exists

# Refinement (re-run Stage 5 with feedback, no MCP needed)
python execute.py --refine PROJ-123 --feedback "Split step 3 into BE and FE"
```

---

## Key Modules

| Module | Purpose |
|--------|---------|
| `execute.py` | CLI entry point, pipeline orchestration, issue locking |
| `src/executor/phases/context_builder.py` | Stages 1–4: Jira enrichment, Confluence retrieval, GitHub gate + context, aggregation |
| `src/executor/phases/llm_executor.py` | Stage 5: LLM API calls with retry, response validation, output file generation |
| `src/executor/phases/phase_zero.py` | Phase 0 + Phase 0.5: backlog analysis, feedback incorporation, validation gate |
| `src/executor/phases/story_creator.py` | Idempotent Jira story creation with manifest and duplicate detection |
| `src/executor/phases/decomposition.py` | Extract stories from LLM response, layer taxonomy (BE/FE/INFRA/DB/QA/DOCS/GEN) |
| `src/executor/phases/validation.py` | Work plan validation rules (structure, dependencies, quality) |
| `src/executor/phases/post_execution.py` | Post-pipeline: Jira comments, status transitions, review task creation |
| `src/executor/phases/status_checker.py` | Per-status artifact validation and auto-fix |
| `src/executor/phases/context_store.py` | Serialize/deserialize `ExecutionContext` for `--refine` mode |
| `src/executor/mcp/client.py` | MCP client manager — lifecycle for Jira, Confluence, GitHub servers |
| `src/executor/mcp/servers/jira_server.py` | Custom Jira MCP server (REST API, ADF↔Markdown conversion) |
| `src/executor/mcp/servers/confluence_server.py` | Custom Confluence MCP server (CQL search, page retrieval) |
| `src/executor/models/execution_context.py` | Core dataclasses: `ExecutionContext`, `JiraContext`, `RefinedConfluenceContext` |
| `src/executor/models/github_models.py` | GitHub dataclasses: `GitHubContext`, `GitHubFetchDecision`, `RepoStatus` |
| `src/executor/prompts/system_prompt.py` | System prompt for DeepSeek |
| `src/executor/prompts/user_prompt.py` | User prompt with context injection |
| `src/executor/prompts/phase_zero_prompt.py` | Phase 0 prompt templates |
| `src/executor/prompts/phase_zero_feedback_prompt.py` | Phase 0.5 prompt templates |
| `src/executor/utils/issue_lock.py` | File-based issue locking (`acquire_issue_lock`) |
| `src/executor/utils/file_utils.py` | `atomic_write()` helper |
| `src/executor/utils/config_loader.py` | YAML config loader |

---

## Data Flow

```
JiraContext + RefinedConfluenceContext + GitHubContext
    → ExecutionContext
        → LLM prompt (system + user)
            → LLM response
                → Validation
                    → Work Plan + DecompositionResult
                        → Jira child Stories (BE/FE/INFRA/DB/QA/DOCS/GEN)
```

---

## Configuration

**Environment (`.env`):** Atlassian URL, bot/admin credentials (email + API token), DeepSeek API key, GitHub token.

**Workflow config (`config/sdlc_config.yaml`):** Confluence structure (space, page titles), Jira workflow statuses and custom field mappings, GitHub extraction settings (config files, key directories, commit/PR limits, deduplication topics), layer taxonomy, LLM parameters (model: `deepseek-chat`, temperature: 0.2, timeout: 120s, retry: 3 attempts), quality gates (DoR, architecture gate, DoD).

---

## Output Files

Generated in `outputs/{ISSUE_KEY}/`:

- `{KEY}_context.md` — aggregated context sent to LLM
- `{KEY}_prompt.md` — full LLM prompt
- `{KEY}_reasoning.md` — LLM raw response
- `{KEY}_plan.md` — extracted work plan
- `{KEY}_metrics.md` — token usage, timing, model info
- `{KEY}_selection_log.md` — Confluence document selection reasoning
- `{KEY}_phase0.md` — Phase 0 analysis
- `{KEY}_phase05.md` — Phase 0.5 updated analysis
- `{KEY}_context_store.json` — serialized ExecutionContext for `--refine`
- `{KEY}_stories_manifest.json` — story creation outcomes for idempotency

---

## Code Style

- Python 3.11+
- Line length: 100 (Black + Ruff)
- Type checking: MyPy strict mode
- Data models: Pydantic v2 and dataclasses
- Tests: pytest (`tests/unit/` for unit, `tests/test_mcp_integration.py` for integration)

---

## Known Limitations (not yet addressed)

These are documented gaps that have not been fixed:
- No reminder/timeout mechanism when assignee doesn't respond to Phase 0 questions
- Non-assignee comments (from tech lead, QA) are ignored in Phase 0.5
- No partial DoR progress tracking (binary: all BLOCKING resolved or not)
- No cross-validation of LLM-referenced files against actual repo structure
- Confluence pagination limited to ~20 documents per query
- Created child stories don't inherit parent fields (assignee, sprint, priority, labels)
- No story count limit in decomposition
- No effort estimation (story points / t-shirt sizing) on generated stories
- Context store version mismatch only logs a warning, doesn't fail or migrate
