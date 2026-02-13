# AI-Swarm

AI-driven SDLC Orchestrator — a multi-agent system that automates software development lifecycle phases.

## What is AI-Swarm?

AI-Swarm is an **Executor Agent** that orchestrates AI agents to automate analysis, planning, and implementation phases of software development. It integrates with Jira, Confluence, and GitHub via MCP (Model Context Protocol) servers and uses LLM (DeepSeek) for intelligent document selection and work plan generation.

### Core Principles

1. **SSOT (Single Source of Truth)**
   - Documentation system = State (Architecture, Contracts, Logic)
   - Version control = Implementation (Code)
   - Task tracker = Flow (Process management)

2. **Spec-First Development**
   - No code execution without approved architectural specification
   - Human review gates between phases

3. **Two-Stage Retrieval**
   - Mandatory core documents (Project Passport, Logical Architecture)
   - LLM-filtered supporting documents based on task relevance

4. **Transparent Agent Identity**
   - All agent actions are traceable
   - Clear audit trail in all systems

## Architecture

![Pipeline Architecture](docs/diagrams/pipeline.puml)

```
┌─────────────────────────────────────────────────────────────────────┐
│                        CLI Entry Point                               │
│                   python execute.py --task PROJ-123                   │
└───────────────────────────────┬─────────────────────────────────────┘
                                │
┌───────────────────────────────▼─────────────────────────────────────┐
│                         5-Stage Pipeline                             │
│                                                                      │
│  ┌──────────┐   ┌──────────┐   ┌──────────┐   ┌──────────┐   ┌────────────┐
│  │ Stage 1  │──▶│ Stage 2  │──▶│ Stage 3  │──▶│ Stage 4  │──▶│  Stage 5   │
│  │ Trigger  │   │  Jira    │   │Knowledge │   │Aggregate │   │    LLM     │
│  │          │   │Enrichment│   │ Retrieval│   │ Context  │   │ Execution  │
│  └──────────┘   └──────────┘   └──────────┘   └──────────┘   └────────────┘
│       │              │              │              │               │
│   Parse Key     Extract:      3a: Confluence  Build         DeepSeek API
│                 - Summary     3b: GitHub      Unified       - Work Plan
│                 - Desc        (deduped)       Context       - DoR Check
│                 - Project                                   - Concerns
│                 - Comments
└─────────────────────────────────────────────────────────────────────┘
                                │
┌───────────────────────────────▼─────────────────────────────────────┐
│                      MCP Integration Layer                           │
│                                                                      │
│  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐      │
│  │  Jira MCP       │  │ Confluence MCP  │  │  GitHub MCP     │      │
│  │  - get_issue    │  │ - get_space     │  │  - get_contents │      │
│  │  - get_comments │  │ - get_page      │  │  - list_commits │      │
│  │  - ADF→Markdown │  │ - search_pages  │  │  - search_code  │      │
│  └─────────────────┘  └─────────────────┘  └─────────────────┘      │
│         Custom              Custom          Official MCP Server      │
└─────────────────────────────────────────────────────────────────────┘
```

## Pipeline Stages

| Stage | Name | Description |
|-------|------|-------------|
| 1 | **Trigger** | Parse Jira issue key from input (e.g., `PROJ-123` or URL) |
| 2 | **Jira Enrichment** | Extract summary, description, comments, custom fields (Project, Component) |
| 3a | **Confluence Knowledge** | Two-Stage Retrieval: fetch core docs + LLM-filtered supporting docs |
| 3b | **GitHub Context** | Extract codebase structure, configs, commits (with Confluence deduplication) |
| 4 | **Data Aggregation** | Build unified `ExecutionContext` for LLM |
| 5 | **LLM Execution** | Generate work plan via DeepSeek with Definition of Ready check |

## Knowledge Retrieval (Stage 3)

### Stage 3a: Confluence Two-Stage Retrieval

```
Phase 1: Location Resolution (Priority Order)
    1. "Project Link" field (direct Confluence URL) → Extract folder ID
    2. "Project Folder" field (search fallback) → CQL search
    └── Example URL: https://your-domain.atlassian.net/wiki/spaces/PROJ/folder/123456

Phase 2: Mandatory Path (Core Docs)
    └── Search "Project Passport" → REQUIRED
    └── Search "Logical Architecture" → REQUIRED
    └── If missing → project_status = NEW_PROJECT

Phase 3: Discovery Path (LLM Filter) — Only for EXISTING projects
    └── CQL search for related pages
    └── DeepSeek filters candidates (CTO role)
    └── Fetch selected supporting documents
```

**Jira Custom Fields** (configurable — see `config/sdlc_config.example.yaml`):

| Field | Type | Purpose |
|-------|------|---------|
| `Project Link` | URL/Text | Direct link to Confluence project folder |
| `Project` | Dropdown | Legacy: folder name for search |

### Stage 3b: GitHub Context Extraction

```
Phase 1: URL Discovery (Priority Order)
    └── Jira description (inlineCard smart links)
    └── Confluence "Project Passport" page
    └── Confluence "Logical Architecture" page

Phase 2: Repository Context
    └── Repository structure (tree format)
    └── Configuration files (pyproject.toml, package.json, etc.)
    └── Recent commits (last 10)

Phase 3: Confluence Deduplication
    └── Skip topics already covered in Confluence docs
    └── Avoid duplicating architecture, tech stack info
```

**GitHub MCP**: Uses official `@modelcontextprotocol/server-github` via npx.

## Output Files

When you run the pipeline, it generates these files in `outputs/{ISSUE_KEY}/`:

| File | Description |
|------|-------------|
| `{KEY}_context.md` | Aggregated context from Jira + Confluence |
| `{KEY}_selection.md` | LLM document selection reasoning (if candidates exist) |
| `{KEY}_prompt.md` | Full prompt sent to DeepSeek |
| `{KEY}_reasoning.md` | Raw LLM response |
| `{KEY}_plan.md` | Extracted work plan with DoR checklist |

## Project Structure

```
AI-swarm/
├── config/
│   ├── sdlc_config.yaml          # Your config (gitignored)
│   └── sdlc_config.example.yaml  # Template config
├── src/executor/
│   ├── mcp/
│   │   ├── client.py             # MCP client manager (Jira, Confluence, GitHub)
│   │   └── servers/
│   │       ├── jira_server.py        # Jira MCP (ADF→Markdown)
│   │       └── confluence_server.py  # Confluence MCP
│   ├── models/
│   │   ├── execution_context.py      # Pipeline data models
│   │   ├── jira_models.py            # Jira data models
│   │   ├── confluence_models.py      # Confluence data models
│   │   └── github_models.py          # GitHub context models
│   ├── phases/
│   │   ├── context_builder.py        # Stages 1-4 implementation (incl. GitHub)
│   │   └── llm_executor.py           # Stage 5 implementation
│   ├── prompts/
│   │   ├── system_prompt.py          # LLM system prompt
│   │   └── user_prompt.py            # Context → prompt builder
│   └── utils/
│       └── config_loader.py          # YAML config loader
├── docs/
│   ├── diagrams/
│   │   └── pipeline.puml             # PlantUML architecture diagram
│   ├── BOT_ACCOUNT_SETUP.md          # Bot account setup guide
│   └── IMPLEMENTATION_PROMPT.md      # Implementation specification
├── outputs/                      # Generated output files (gitignored)
├── .env.example                  # Environment template
├── execute.py                    # CLI entry point
└── requirements.txt
```

## Quick Start

### 1. Install dependencies

```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 2. Configure environment

```bash
# Copy template files
cp .env.example .env
cp config/sdlc_config.example.yaml config/sdlc_config.yaml
```

Edit `.env` with your credentials and `config/sdlc_config.yaml` with your Jira/Confluence structure. See [BOT_ACCOUNT_SETUP.md](docs/BOT_ACCOUNT_SETUP.md) for creating a dedicated bot account.

### 3. Run the pipeline

```bash
# Full pipeline
python execute.py --task PROJ-123

# Dry-run (skip LLM execution)
python execute.py --task PROJ-123 --dry-run

# Custom output directory
python execute.py --task PROJ-123 --output-dir ./my_outputs
```

## Configuration

### Environment Variables (`.env`)

| Variable | Required | Description |
|----------|----------|-------------|
| `ATLASSIAN_URL` | Yes | Your Atlassian instance URL |
| `CONFLUENCE_URL` | Yes | Confluence URL (must include `/wiki` suffix) |
| `ATLASSIAN_BOT_EMAIL` | Yes | Bot account email |
| `ATLASSIAN_BOT_API_TOKEN` | Yes | Bot API token |
| `DEEPSEEK_API_KEY` | Yes | DeepSeek LLM API key |
| `GITHUB_TOKEN` | No | GitHub PAT (enables codebase context) |
| `JIRA_PROJECT_DROPDOWN_FIELD` | No | Custom field ID for "Project" dropdown |
| `JIRA_PROJECT_LINK_FIELD` | No | Custom field ID for "Project Link" URL |

### Workflow Config (`config/sdlc_config.yaml`)

Customize Jira statuses, Confluence page titles, naming conventions, and quality gates. See `config/sdlc_config.example.yaml` for the full template.

## Development Status

**Current: v0.4.0 — Direct Confluence Link**

- [x] Custom MCP servers (Jira + Confluence)
- [x] Two-Stage Retrieval with LLM filtering
- [x] SelectionLog for transparency
- [x] DeepSeek LLM integration
- [x] Work plan generation with DoR check
- [x] CLI with dry-run mode
- [x] GitHub MCP integration (official server)
- [x] Codebase context extraction (structure, configs, commits)
- [x] Confluence-first deduplication strategy
- [x] **Project Link field** — direct Confluence folder URL (no search needed)
- [x] **Confluence folder-only entity support** — works with Confluence folders
- [ ] Auto-create project documentation (Passport, Architecture)
- [ ] Human review gate (Phase 3)
- [ ] Code generation phase
- [ ] PR creation automation

## License

MIT
