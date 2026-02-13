# AI-SWARM: Context Gathering & LLM Execution Pipeline

## Implementation Task

Build the 5-stage pipeline that transforms a Jira issue into an LLM-generated work plan.

---

## Stage 1: Trigger (Инициация)

**Event:** Receive Jira issue ID via CLI
**Input:** `python execute.py --task PROJ-123`
**Output:** Validated issue key (e.g., `PROJ-123`)

### Implementation
```python
# Already exists in execute.py:30-48
issue_key = parse_jira_key(task_input)  # PROJ-123 or URL → PROJ-123
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
# Priority: label "myproject" → project key "PROJ" → fallback "AI"
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

## Stage 3a: Knowledge Retrieval (Сбор знаний из Confluence)

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

## Stage 3b: GitHub Context Extraction (Сбор контекста из GitHub)

**Input:** `JiraContext` + `ConfluenceContext` (for deduplication)
**Output:** `GitHubContext` with repository structure, configs, commits

### 3b.1 URL Discovery (Priority Order)

```python
# Extract GitHub URL from multiple sources
def extract_github_url(jira_context, confluence_context) -> str | None:
    # Priority 1: Jira description (inlineCard smart links)
    url = extract_from_text(jira_context.description)
    if url:
        return url

    # Priority 2: Confluence Project Passport
    if confluence_context.project_passport:
        url = extract_from_text(confluence_context.project_passport)
        if url:
            return url

    # Priority 3: Confluence Logical Architecture
    if confluence_context.logical_architecture:
        url = extract_from_text(confluence_context.logical_architecture)
        if url:
            return url

    return None  # No GitHub URL found → project_status = NEW_PROJECT
```

### 3b.2 Repository Context Extraction

```python
# Extract codebase context using official GitHub MCP
def extract_github_context(mcp, owner, repo) -> GitHubContext:
    # Get repository structure (tree format)
    structure = mcp.github_get_file_contents(owner, repo, "")

    # Get configuration files
    configs = []
    for config_file in ["pyproject.toml", "package.json", "requirements.txt"]:
        try:
            content = mcp.github_get_file_contents(owner, repo, config_file)
            configs.append(ConfigSummary(name=config_file, summary=content[:500]))
        except:
            pass

    # Get recent commits
    commits = mcp.github_list_commits(owner, repo, per_page=10)

    return GitHubContext(
        repository_url=f"https://github.com/{owner}/{repo}",
        status=RepoStatus.EXISTS,
        structure=structure,
        configs=configs,
        recent_commits=commits
    )
```

### 3b.3 Confluence Deduplication

```python
# Skip topics already covered in Confluence
def extract_confluence_topics(confluence_context) -> set[str]:
    """Extract topics from Confluence for deduplication."""
    topics = set()

    # Check Project Passport and Logical Architecture for covered topics
    for doc in [confluence_context.project_passport, confluence_context.logical_architecture]:
        if doc:
            if "technology stack" in doc.lower():
                topics.add("tech_stack")
            if "architecture" in doc.lower():
                topics.add("architecture")
            # ... more topic patterns

    return topics

# Apply deduplication before fetching GitHub content
skipped_topics = extract_confluence_topics(confluence_context)
github_context.skipped_topics = list(skipped_topics)
```

### Output Model

```python
from enum import Enum
from dataclasses import dataclass, field

class RepoStatus(Enum):
    EXISTS = "exists"
    NOT_FOUND = "not_found"
    NEW_PROJECT = "new_project"

@dataclass
class GitHubContext:
    repository_url: str | None = None
    status: RepoStatus = RepoStatus.NEW_PROJECT
    discovery_source: str = "none"  # "jira", "confluence_passport", etc.
    owner: str = ""
    repo_name: str = ""

    # Repository data
    structure: RepoStructure | None = None
    configs: list[ConfigSummary] = field(default_factory=list)
    recent_commits: list[str] = field(default_factory=list)

    # Deduplication tracking
    skipped_topics: list[str] = field(default_factory=list)
```

### MCP Integration

GitHub MCP uses the official `@modelcontextprotocol/server-github` server:

```python
# In MCPClientManager.start_all()
if github_token:
    self.clients["github"] = MCPClient(
        command="npx",
        args=["-y", "@modelcontextprotocol/server-github"],
        env={"GITHUB_PERSONAL_ACCESS_TOKEN": github_token},
    )
```

---

## Stage 4: Data Aggregation (Формирование контекста)

**Input:** `JiraContext` + `ConfluenceContext` + `GitHubContext`
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
# Context for PROJ-123
Generated: 2024-01-15T10:30:00Z

## Jira Data
...

## Confluence Data
...
```

2. **`{issue_key}_prompt.md`** - Full prompt BEFORE sending to LLM (saved for validation)
```markdown
# LLM Prompt for PROJ-123
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
# Agent Reasoning for PROJ-123

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
# Work Plan: PROJ-123

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
├── mcp/
│   ├── client.py             # MCP client manager (Jira, Confluence, GitHub)
│   └── servers/
│       ├── jira_server.py        # Jira MCP (ADF→Markdown)
│       └── confluence_server.py  # Confluence MCP
├── phases/
│   ├── __init__.py
│   ├── context_builder.py    # Stages 1-4: Jira, Confluence, GitHub context
│   └── llm_executor.py       # Stage 5: LLM call, output generation
├── models/
│   ├── execution_context.py  # ExecutionContext model
│   ├── jira_models.py        # Jira data models
│   ├── confluence_models.py  # Confluence data models
│   └── github_models.py      # GitHub context models (NEW)
└── prompts/
    ├── __init__.py
    ├── system_prompt.py      # System prompt template
    └── user_prompt.py        # User prompt template

outputs/
└── {issue_key}/
    ├── {issue_key}_context.md
    ├── {issue_key}_selection.md
    ├── {issue_key}_prompt.md
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

- [x] `python execute.py --task PROJ-123` produces output files
- [x] Context includes Jira description + Confluence pages
- [x] GitHub context included when GITHUB_TOKEN is set
- [x] Confluence-first deduplication prevents duplicate info
- [x] LLM response follows structured format
- [x] Uncertainties are clearly marked
- [x] Work plan has actionable steps with layers

---

## Dependencies

```python
# requirements.txt additions
openai>=1.0.0  # DeepSeek uses OpenAI-compatible API
```

## Environment Variables

```bash
# .env

# Required
JIRA_URL=https://your-domain.atlassian.net
JIRA_EMAIL=your-email@example.com
JIRA_API_TOKEN=your-jira-api-token

CONFLUENCE_URL=https://your-domain.atlassian.net/wiki
CONFLUENCE_EMAIL=your-email@example.com
CONFLUENCE_API_TOKEN=your-confluence-api-token

DEEPSEEK_API_KEY=sk-...  # Required for Stage 5 and Two-Stage Retrieval

# Optional (Stage 3b: GitHub context)
GITHUB_TOKEN=ghp_...  # GitHub Personal Access Token
```

## DeepSeek API Notes

- **Base URL:** `https://api.deepseek.com/v1`
- **Models:** `deepseek-chat` (general) or `deepseek-coder` (code-focused)
- **API Format:** OpenAI-compatible (uses `openai` Python package)
- **Rate Limits:** Check DeepSeek documentation
- **Pricing:** ~$0.14/1M input tokens, ~$0.28/1M output tokens
