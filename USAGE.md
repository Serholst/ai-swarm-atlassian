# AI-SWARM Usage Guide (MVP)

## Quick Start

### 1. Execute a Feature

```bash
# Using issue key
python3 execute.py --task WEB3-3

# Using full Jira URL
python3 execute.py --task "https://your-domain.atlassian.net/browse/WEB3-3"

# Short form
python3 execute.py -t WEB3-3
```

### 2. Run Integration Tests

```bash
# Activate virtual environment first
source venv/bin/activate

# Run all tests including WEB3-3
python tests/test_mcp_integration.py

# Or use the test runner
./run_tests.sh
```

## Current Capabilities (MVP v0.1.0)

### ✅ What Works

1. **Jira Integration**
   - Fetch any feature by key or URL
   - Clean ADF → Markdown conversion
   - Full issue details (status, assignee, description, etc.)

2. **MCP Servers**
   - Jira MCP server with 6 tools
   - Confluence MCP server with 5 tools
   - Proper JSON-RPC protocol implementation
   - Automatic initialization handshake

3. **Data Models**
   - Pydantic validation for all data
   - Type-safe models (JiraIssue, ConfluencePage, etc.)

4. **CLI Interface**
   - Simple command-line execution
   - Supports both issue keys and URLs
   - Rich formatted output

### ❌ Not Yet Implemented

1. **Workflow Automation**
   - State machine (skeleton only)
   - Phase handlers (not implemented)
   - Automated transitions

2. **AI Integration**
   - Anthropic Claude API (SDK installed, not integrated)
   - Plan generation
   - Code generation

3. **GitHub Integration**
   - PR creation
   - Branch management
   - Code commits

4. **Confluence Write Operations**
   - Create pages
   - Update documentation
   - Publish specs

## Examples

### Execute WEB3-3 Feature

```bash
$ python3 execute.py --task WEB3-3

Parsed issue key: WEB3-3

╭────────────────────────────╮
│ AI-SWARM Executor (MVP)    │
│ Processing Feature: WEB3-3 │
╰────────────────────────────╯

Step 1: Loading Environment...
✓ Environment loaded

Step 2: Loading Configuration...
✓ Configuration loaded

Step 3: Starting MCP Servers...
✓ MCP servers started

Step 4: Fetching Feature WEB3-3...
✓ Feature retrieved successfully

======================================================================
Feature Details
======================================================================
                               WEB3-3: test_type

Type: Feature Status: Backlog Project: holst_test_project (WEB3)
Assignee: Sergey Kholstov Labels: None

Description

[No description]

Metadata

 • Created: 2026-01-25 15:30:46.750000-03:00
 • Updated: 2026-01-25 15:30:47.414000-03:00
 • Parent: None
 • Subtasks: None

======================================================================

MVP Mode: Feature retrieved but not processed
Full SDLC automation coming in next version...

Step 5: Stopping MCP Servers...
✓ MCP servers stopped
```

### Using Full URL

```bash
$ python3 execute.py --task "https://your-domain.atlassian.net/browse/WEB3-3"

# Same output as above
```

### Help

```bash
$ python3 execute.py --help

usage: execute.py [-h] --task TASK [--dry-run]

AI-SWARM Executor - Automated SDLC Feature Execution

options:
  -h, --help            show this help message and exit
  --task TASK, -t TASK  Jira issue key or URL (e.g., WEB3-3 or
                        https://your-domain.atlassian.net/browse/WEB3-3)
  --dry-run            Dry run mode (fetch only, no modifications)

Examples:
  python3 execute.py --task WEB3-3
  python3 execute.py --task https://your-domain.atlassian.net/browse/WEB3-3
  python3 execute.py -t WEB3-3
```

## Configuration

All configuration is in:
- `.env` - Credentials and API tokens
- `config/sdlc_config.yaml` - SDLC workflow rules

### Required Environment Variables

```env
JIRA_URL=https://your-domain.atlassian.net/
JIRA_EMAIL=your-email@example.com
JIRA_API_TOKEN=your-jira-token

CONFLUENCE_URL=https://your-domain.atlassian.net
CONFLUENCE_EMAIL=your-email@example.com
CONFLUENCE_API_TOKEN=your-confluence-token
```

## Troubleshooting

### Issue: MCP servers fail to start

**Error**: `[Errno 32] Broken pipe`

**Solution**: Fixed in v0.1.0! Make sure you have the latest code:
- Import path corrections in MCP servers
- MCP initialization handshake implemented
- `sys.executable` instead of hardcoded `python`

### Issue: Cannot parse Jira key

**Error**: `Could not parse Jira issue key from: ...`

**Solution**: Use one of these formats:
- `WEB3-3` (project key with number)
- `PROJECT-123` (standard format)
- Full URL: `https://your-domain.atlassian.net/browse/WEB3-3`

### Issue: Confluence pages not found

**Error**: `404 Client Error: Not Found`

**Cause**: The Confluence pages specified in `config/sdlc_config.yaml` don't exist yet:
- "🌌 AI Engineering Hub" (space home)
- "SDLC & Workflows Rules"
- "Product Registry"

**Solution**: Create these pages in Confluence or update the config with existing page titles.

## Next Steps

To continue building the full workflow automation:

1. **Implement State Machine** ([src/executor/core/](src/executor/core/))
   - Phase detection based on Jira status
   - Phase routing logic
   - Transition management

2. **Implement Phase Handlers** ([src/executor/phases/](src/executor/phases/))
   - Phase 1: Context Loading
   - Phase 2: Planning (with Claude)
   - Phase 4: Devementation (code generation)
   - Phase 5: Review & Finalization

3. **Add GitHub Integration**
   - Create GitHub API client
   - PR creation and management
   - Branch operations

4. **Integrate Claude API**
   - Plan generation from requirements
   - Code generation from specs
   - Story breakdown

## Support

For issues or questions:
- Check [README.md](README.md) for architecture details
- Review [QUICKSTART.md](QUICKSTART.md) for setup
- See [BOT_ACCOUNT_SETUP.md](BOT_ACCOUNT_SETUP.md) for bot configuration

## Version History

### v0.1.0 (Current - MVP)
- ✅ MCP servers (Jira + Confluence)
- ✅ Data models and validation
- ✅ Simple CLI executor
- ✅ Integration tests
- ❌ Workflow automation (planned)
- ❌ AI integration (planned)
- ❌ GitHub integration (planned)
