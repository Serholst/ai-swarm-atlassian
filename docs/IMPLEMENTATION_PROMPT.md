# AI-SWARM: Context Gathering & LLM Execution Pipeline

## Implementation Task

Build the 5-stage pipeline that transforms a Jira issue into an LLM-generated work plan.

---

## Stage 1: Trigger (Инициация)

**Event:** Receive Jira issue ID via CLI
**Input:** `python execute.py --task WEB3-6`
**Output:** Validated issue key (e.g., `WEB3-6`)

### Implementation
```python
# Already exists in execute.py:30-48
issue_key = parse_jira_key(task_input)  # WEB3-6 or URL → WEB3-6
```

### Validation Rules
- Format: `[A-Z0-9]+-\d+` (PROJECT-123)
- Extract from URL if provided
- Fail fast if invalid format

---

## Stage 2: Jira Enrichment (Обогащение из Jira)

**Input:** Issue key
**Output:** Structured `JiraContext` with all relevant fields

### Data to Extract
| Field | Source | Purpose |
|-------|--------|---------|
| `description` | `fields.description` | Task requirements |
| `summary` | `fields.summary` | Task title |
| `project_key` | `fields.project.key` | Confluence space lookup |
| `project_name` | `fields.project.name` | Human-readable name |
| `components` | `fields.components[]` | Technical classification |
| `labels` | `fields.labels[]` | **Confluence space identifier** |
| `issue_type` | `fields.issuetype.name` | Feature/Story/Task |
| `status` | `fields.status.name` | Current workflow state |
| `assignee` | `fields.assignee` | Owner |
| `parent_key` | `fields.parent.key` | Hierarchy link |

### Key Logic
```python
# Extract Confluence space key from labels or project
# Priority: label "web3" → project key "WEB3" → fallback "AI"
confluence_space_key = extract_space_key(issue.labels, issue.project.key)
```

### Output Model
```python
@dataclass
class JiraContext:
    issue_key: str
    summary: str
    description: str  # Cleaned Markdown
    project_key: str
    project_name: str
    components: list[str]
    labels: list[str]
    issue_type: str
    status: str
    assignee: str | None
    parent_key: str | None
    confluence_space_key: str  # Derived from labels/project
```

---

## Stage 3: Knowledge Retrieval (Сбор знаний из Confluence)

**Input:** `confluence_space_key` from Stage 2
**Output:** `ConfluenceContext` with space content + SDLC rules

### 3.1 Primary Search: Space Root Page
```python
# Get space homepage content
root_page = mcp.confluence_get_space_home(space_key)
# Returns: title, body (Markdown), url, labels
```

### 3.2 Secondary Search: SDLC Rules Page
```python
# Search for SDLC rules in the space or global
cql = f'space = "{space_key}" AND title ~ "SDLC" ORDER BY lastmodified DESC'
sdlc_pages = mcp.confluence_search_pages(cql, limit=1)

# Fallback: Global SDLC rules
if not sdlc_pages:
    sdlc_page = mcp.confluence_get_page(
        space_key="AI",  # Global rules space
        title="SDLC & Workflows Rules"
    )
```

### 3.3 Optional: Project Passport
```python
# If project has passport page
passport = mcp.confluence_get_project_passport(space_key, project_name)
```

### Output Model
```python
@dataclass
class ConfluenceContext:
    space_key: str
    space_name: str

    # Root page content
    root_page_title: str
    root_page_content: str  # Markdown
    root_page_url: str

    # SDLC Rules
    sdlc_rules_content: str  # Markdown
    sdlc_rules_url: str

    # Optional enrichment
    project_passport: str | None  # Markdown
    logical_architecture: str | None  # Markdown
```

---

## Stage 4: Data Aggregation (Формирование контекста)

**Input:** `JiraContext` + `ConfluenceContext`
**Output:** Unified `ExecutionContext` ready for LLM

### Aggregation Structure
```python
@dataclass
class ExecutionContext:
    # Metadata
    issue_key: str
    timestamp: datetime

    # Jira Data
    jira: JiraContext

    # Confluence Data
    confluence: ConfluenceContext

    # Aggregated Prompt Context (for LLM)
    prompt_context: str  # Formatted Markdown

    def build_prompt_context(self) -> str:
        """Build unified context for LLM prompt."""
        return f"""
# Task Context

## Jira Issue: {self.jira.issue_key}

**Title:** {self.jira.summary}
**Type:** {self.jira.issue_type}
**Status:** {self.jira.status}
**Project:** {self.jira.project_name} ({self.jira.project_key})
**Components:** {', '.join(self.jira.components) or 'None'}
**Labels:** {', '.join(self.jira.labels) or 'None'}

### Description

{self.jira.description}

---

## Project Knowledge Base

### Space: {self.confluence.space_name}

{self.confluence.root_page_content}

---

## SDLC & Workflow Rules

{self.confluence.sdlc_rules_content}

---

## Project Passport

{self.confluence.project_passport or '[Not available]'}
"""
```

---

## Stage 5: LLM Execution (Исполнение)

**Input:** `ExecutionContext.prompt_context`
**Output:** Work plan `.md` file with reasoning and plan

### LLM Configuration (DeepSeek API)
```yaml
agent:
  provider: "deepseek"
  model: "deepseek-chat"  # or "deepseek-coder" for code tasks
  api_base: "https://api.deepseek.com/v1"
  temperature: 0.2
  max_tokens: 8192
  min_confidence: 0.7
  require_citations: true
```

### DeepSeek API Integration
```python
from openai import OpenAI  # DeepSeek uses OpenAI-compatible API

client = OpenAI(
    api_key=os.getenv("DEEPSEEK_API_KEY"),
    base_url="https://api.deepseek.com/v1"
)

response = client.chat.completions.create(
    model="deepseek-chat",
    messages=[
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt}
    ],
    temperature=0.2,
    max_tokens=8192
)
```

### System Prompt
```python
SYSTEM_PROMPT = """
You are an AI SDLC Executor Agent. Your task is to analyze a Jira issue
and create a detailed work plan following the project's SDLC rules.

## Your Responsibilities

1. **Analyze** the task requirements from the Jira description
2. **Review** the project context from Confluence
3. **Follow** the SDLC & Workflow Rules strictly
4. **Produce** a structured work plan

## Output Format

Your response MUST follow this structure:

### 1. Understanding (Понимание задачи)
- What is being asked?
- What are the acceptance criteria?
- What are the constraints?

### 2. Concerns & Uncertainties (Сомнения и неясности)
- List any ambiguities in requirements
- Missing information needed
- Technical risks identified
- Questions for human review

### 3. Analysis (Анализ)
- Technical approach
- Components affected
- Dependencies identified
- Estimated complexity (S/M/L/XL)

### 4. Work Plan (План работ)
For each step, provide:
- [ ] **Step N:** [Action description]
  - Layer: [BE/FE/INFRA/DB/QA/DOCS]
  - Files: [Expected files to modify]
  - Acceptance: [How to verify completion]

### 5. Definition of Ready Checklist
- [ ] Clear Goal: Description is unambiguous
- [ ] Decomposition Clarity: Technical steps understood
- [ ] Resources Located: Confluence pages accessible
- [ ] Resources Located: GitHub repo accessible

## Rules
- Do NOT hallucinate information not present in the context
- Mark missing data as [DATA MISSING]
- Cite sources from Confluence when referencing rules
- If task is unclear, list specific questions in Concerns section
"""
```

### User Prompt
```python
USER_PROMPT = """
Analyze the following task and create a work plan:

{execution_context.prompt_context}

---

Create a detailed work plan following the SDLC rules provided above.
Focus on actionable steps that can be executed.
"""
```

### Output Files

**Location:** `outputs/{issue_key}/`

**Files Generated (in order):**

1. **`{issue_key}_context.md`** - Raw aggregated context (Stage 4 output)
```markdown
# Context for WEB3-6
Generated: 2024-01-15T10:30:00Z

## Jira Data
...

## Confluence Data
...
```

2. **`{issue_key}_prompt.md`** - Full prompt BEFORE sending to LLM (saved for validation)
```markdown
# LLM Prompt for WEB3-6
Generated: 2024-01-15T10:30:00Z
Model: deepseek-chat
Temperature: 0.2

---

## System Prompt

[Full system prompt content]

---

## User Prompt

[Full user prompt with context]
```

3. **`{issue_key}_reasoning.md`** - LLM response: chain-of-thought
```markdown
# Agent Reasoning for WEB3-6

## Understanding
...

## Concerns & Uncertainties
- Uncertainty 1: [description]
- Uncertainty 2: [description]

## Analysis
...
```

4. **`{issue_key}_plan.md`** - Final work plan (extracted from LLM response)
```markdown
# Work Plan: WEB3-6

## Summary
[One-line summary]

## Steps

- [ ] **Step 1:** [Action]
  - Layer: BE
  - Files: src/api/handler.py
  - Acceptance: Unit tests pass

- [ ] **Step 2:** [Action]
  ...

## Definition of Ready
- [x] Clear Goal
- [ ] Resources Located (GitHub missing)
...
```

### Execution Flow
```
Stage 4 → Save context.md
        → Build prompt
        → Save prompt.md (BEFORE LLM call)
        → Call DeepSeek API
        → Save reasoning.md
        → Extract plan → Save plan.md
```

---

## File Structure

```
src/executor/
├── phases/
│   ├── __init__.py
│   ├── context_builder.py    # Stages 1-4: JiraContext, ConfluenceContext, ExecutionContext
│   └── llm_executor.py       # Stage 5: LLM call, output generation
├── models/
│   ├── execution_context.py  # New: ExecutionContext model
│   └── ...
└── prompts/
    ├── __init__.py
    ├── system_prompt.py      # System prompt template
    └── user_prompt.py        # User prompt template

outputs/
└── {issue_key}/
    ├── {issue_key}_context.md
    ├── {issue_key}_reasoning.md
    └── {issue_key}_plan.md
```

---

## Implementation Order

1. **Create models** (`execution_context.py`)
   - `JiraContext`
   - `ConfluenceContext`
   - `ExecutionContext`

2. **Build context_builder.py**
   - `extract_jira_context(mcp, issue_key) -> JiraContext`
   - `extract_confluence_context(mcp, space_key) -> ConfluenceContext`
   - `build_execution_context(jira, confluence) -> ExecutionContext`

3. **Build llm_executor.py**
   - `execute_llm(context: ExecutionContext) -> LLMResponse`
   - `save_outputs(issue_key, context, response) -> list[Path]`

4. **Update execute.py**
   - Replace MVP display with full pipeline
   - Add `--output-dir` option
   - Add `--dry-run` for context-only mode

---

## Success Criteria

- [ ] `python execute.py --task WEB3-6` produces 3 output files
- [ ] Context includes Jira description + Confluence pages
- [ ] LLM response follows structured format
- [ ] Uncertainties are clearly marked
- [ ] Work plan has actionable steps with layers

---

## Dependencies

```python
# requirements.txt additions
openai>=1.0.0  # DeepSeek uses OpenAI-compatible API
```

## Environment Variables

```bash
# .env (required for Stage 5)
DEEPSEEK_API_KEY=sk-...  # DeepSeek API key
```

## DeepSeek API Notes

- **Base URL:** `https://api.deepseek.com/v1`
- **Models:** `deepseek-chat` (general) or `deepseek-coder` (code-focused)
- **API Format:** OpenAI-compatible (uses `openai` Python package)
- **Rate Limits:** Check DeepSeek documentation
- **Pricing:** ~$0.14/1M input tokens, ~$0.28/1M output tokens
