# AI SDLC Executor

AI-driven SDLC Orchestrator - Executor Agent following strict workflow protocol.

## Overview

This project implements an **Executor Agent** that automates the analysis phase of software development using a swarm of AI agents. It follows a strict SDLC workflow protocol defined in Confluence.

### Key Principles

1. **SSOT Doctrine (Single Source of Truth)**:
   - **Confluence** = State (Architecture, Contracts, Logic)
   - **GitHub** = Devementation (Execution)
   - **Jira** = Flow (Process management)

2. **No Local Storage**: All data lives in Jira, Confluence, and GitHub - no local caching

3. **Custom MCP Servers**: Data cleaning at source to prevent "garbage in, garbage out"

4. **Spec-First**: No code execution without approved architectural spec

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                     CLI Entry Point                         │
│                  orchestrator execute --key AI-123          │
└─────────────────────────┬───────────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────────┐
│                   Executor Agent Core                       │
│              ┌──────────────────────────┐                   │
│              │   State Machine Engine   │                   │
│              │   (Phase Router)         │                   │
│              └──────────────────────────┘                   │
└─────────────────────────┬───────────────────────────────────┘
                          │
         ┌────────────────┼────────────────┐
         ▼                ▼                ▼
┌─────────────┐  ┌─────────────┐  ┌─────────────┐
│   Phase 1   │  │   Phase 2   │  │   Phase 4   │
│   Context   │  │  Planning   │  │Devementation│
│   Loading   │  │ (WRITE-LOCK)│  │             │
└─────────────┘  └─────────────┘  └─────────────┘
         │                │                │
         └────────────────┼────────────────┘
                          ▼
┌─────────────────────────────────────────────────────────────┐
│              MCP Integration Layer                          │
│    ┌──────────────────┐      ┌──────────────────┐          │
│    │  Jira MCP Server │      │Confluence MCP    │          │
│    │  (Data Cleaning) │      │Server (Cleaning) │          │
│    └──────────────────┘      └──────────────────┘          │
└─────────────────────────┬───────────────────────────────────┘
                          │
         ┌────────────────┼────────────────┐
         ▼                ▼                ▼
    ┌────────┐       ┌────────┐       ┌────────┐
    │  Jira  │       │Conflue │       │ GitHub │
    │ (Flow) │       │ (SSOT) │       │ (Code) │
    └────────┘       └────────┘       └────────┘
```

## Project Structure

```
AI-swarm/
├── config/
│   └── sdlc_config.yaml          # SDLC workflow configuration
│
├── src/executor/
│   ├── cli/                      # CLI entry point (future)
│   ├── core/                     # State machine & phase router
│   ├── phases/                   # Phase handlers (1, 2, 4, 5)
│   ├── mcp/
│   │   ├── client.py             # MCP client manager
│   │   └── servers/
│   │       ├── confluence_server.py  # Custom Confluence MCP
│   │       └── jira_server.py        # Custom Jira MCP
│   ├── models/
│   │   ├── jira_models.py        # Cleaned Jira data models
│   │   ├── confluence_models.py  # Cleaned Confluence models
│   │   └── workflow_state.py     # Workflow state machine
│   └── utils/
│       ├── html_cleaner.py       # HTML → Markdown cleaning
│       ├── config_loader.py      # Config management
│       └── markdown_formatter.py # Jira formatting
│
└── tests/
    └── test_mcp_integration.py   # MCP integration tests
```

## Setup

### 1. Install Dependencies

```bash
# Using Poetry (recommended)
poetry install

# Or using pip
pip install -r requirements.txt
```

### 2. Configure Environment

```bash
cp .env.example .env
# Edit .env with your credentials
```

Required environment variables:

```env
JIRA_URL=https://yourcompany.atlassian.net
JIRA_EMAIL=bot@company.com
JIRA_API_TOKEN=your_jira_api_token

CONFLUENCE_URL=https://yourcompany.atlassian.net/wiki
CONFLUENCE_EMAIL=bot@company.com
CONFLUENCE_API_TOKEN=your_confluence_api_token

ANTHROPIC_API_KEY=your_anthropic_key
```

### 3. Verify Confluence Space Structure

Ensure your Confluence space follows this structure:

```
📂 🌌 AI Engineering Hub (Space Home)
├── 📄 SDLC & Workflows Rules
├── 📄 Master Team Directory
├── 📂 Product Registry
│   ├── 📂 [Project] AI Orchestrator
│   │   ├── 📄 Project Passport
│   │   └── 📄 Logical Architecture
│   └── 📂 [Project] LP Provider
│       ├── 📄 Project Passport
│       └── 📄 Logical Architecture
└── 📂 System Meta-Backlog
```

## Testing

### Run Integration Tests

```bash
# Test MCP servers and connections
python tests/test_mcp_integration.py

# Test specific Jira issue
python tests/test_mcp_integration.py AI-123
```

This will validate:
- ✅ Confluence connection and data retrieval
- ✅ Jira connection and data retrieval
- ✅ SDLC Rules page existence
- ✅ Data cleaning (HTML → Markdown)
- ✅ SDLC compliance checks

### Expected Output

```
╭───────────────────────────────────────────────────────╮
│     MCP Integration Test Suite                        │
│     Testing Confluence & Jira MCP Servers             │
╰───────────────────────────────────────────────────────╯

Step 1: Loading Environment...
✓ Environment loaded

Step 2: Loading Configuration...
✓ Configuration loaded

Step 3: Starting MCP Servers...
✓ MCP servers started

Testing Confluence Connection...
✓ Confluence connection successful

Testing SDLC Rules Page Retrieval...
✓ SDLC Rules page found

Testing Jira Connection...
✓ Jira connection successful

Testing Data Cleaning...
✓ Data cleaning appears to be working

Validating SDLC Compliance...
✓ All SDLC compliance checks passed

✓ All tests passed!
```

## Custom MCP Servers

### Confluence MCP Server

**Tools provided:**
- `confluence_get_page`: Get page by ID or title (cleaned Markdown)
- `confluence_search_pages`: Search pages using CQL
- `confluence_get_space_home`: Get space homepage
- `confluence_get_project_passport`: Get and parse Project Passport

**Data Cleaning:**
- Removes Confluence-specific macros (`{panel}`, `{code}`, etc.)
- Converts HTML to clean Markdown
- Extracts structured data from tables

### Jira MCP Server

**Tools provided:**
- `jira_get_issue`: Get issue by key (cleaned)
- `jira_search_issues`: Search issues using JQL
- `jira_get_comments`: Get comments (cleaned)
- `jira_add_comment`: Add comment
- `jira_transition_issue`: Change status
- `jira_create_issue`: Create new issue

**Data Cleaning:**
- Converts ADF (Atlassian Document Format) to Markdown
- Cleans rich text to plain text
- Normalizes user/status/type data

## Workflow Phases

### Phase 1: Context Loading
- **Trigger**: Feature → "AI-TO-DO"
- **Actions**: Validate DoR, read Project Passport

### Phase 2: Planning (WRITE-LOCK)
- **Status**: "Analysis"
- **Actions**: Load Confluence context, generate plan, publish DRAFT to Jira comment
- **Critical**: NO writes to Confluence in this phase!

### Phase 3: Human Review
- **Status**: "Human Plan Review"
- **Actions**: Human approves/rejects plan

### Phase 4: Devementation
- **Status**: "In Progress"
- **Actions**: Publish approved plan to Confluence, create Stories, implement code

### Phase 5: Review & Finalization
- **Status**: "Review" → "Deployment" → "Done"
- **Actions**: Code review, merge PR, close Stories

## Configuration

See [config/sdlc_config.yaml](config/sdlc_config.yaml) for full configuration options.

Key settings:
- **Workflow statuses**: Exact Jira status names
- **Naming conventions**: [LAYER] taxonomy (BE, FE, INFRA, DB, QA, DOCS)
- **Quality gates**: DoR, Architecture Gate, DoD
- **Error handling**: Type A (self-correct) vs Type B (escalate)

## Development Status

**Current Iteration (v0.1.0):**
- ✅ Project structure created
- ✅ Data models implemented (Pydantic)
- ✅ Custom MCP servers (Confluence & Jira)
- ✅ HTML/ADF cleaning utilities
- ✅ Integration test suite
- 🚧 State machine engine (in progress)
- 🚧 Phase handlers (in progress)
- 🚧 CLI interface (planned)

**Next Steps:**
1. Implement state machine and phase handlers
2. Build CLI interface
3. Test end-to-end workflow
4. Production deployment

## License

MIT

## Contributing

This project follows strict SDLC protocol. See [SDLC & Workflows Rules](docs/SDLC_RULES.md) for contribution guidelines.
