# Quick Start

Get AI-Swarm running in 5 steps.

## Prerequisites

- Python 3.11+
- Jira Cloud + Confluence Cloud (API access)
- Anthropic API key

## Step 1: Setup Environment

```bash
# Clone and enter directory
cd AI-swarm

# Create virtual environment
python3 -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

Or use the setup script:

```bash
./setup.sh
```

## Step 2: Configure Credentials

```bash
cp .env.example .env
```

Edit `.env` with your credentials:

```env
# Task Tracker (Jira)
JIRA_URL=https://your-domain.atlassian.net
JIRA_EMAIL=bot@yourcompany.com
JIRA_API_TOKEN=your_api_token

# Documentation (Confluence)
CONFLUENCE_URL=https://your-domain.atlassian.net
CONFLUENCE_EMAIL=bot@yourcompany.com
CONFLUENCE_API_TOKEN=your_api_token

# AI (Anthropic)
ANTHROPIC_API_KEY=sk-ant-...
```

**Recommendation**: Use a dedicated bot account for clear audit trails.

## Step 3: Configure Workflow

Edit `config/sdlc_config.yaml` to match your setup:
- Confluence space and page names
- Jira workflow statuses
- Project naming conventions

## Step 4: Verify Setup

```bash
# Run integration tests
./run_tests.sh

# Or manually
python tests/test_mcp_integration.py
```

Expected output:
```
✓ Environment loaded
✓ Configuration loaded
✓ MCP servers started
✓ Confluence connection successful
✓ Jira connection successful
✅ All tests passed!
```

## Step 5: Run Executor

```bash
# Execute on a task
python execute.py --task PROJECT-123

# Dry run (no modifications)
python execute.py --task PROJECT-123 --dry-run
```

## Troubleshooting

| Error | Solution |
|-------|----------|
| `401 Unauthorized` | Check API token and email |
| `Page not found` | Verify Confluence page titles in config |
| `Module not found` | Activate venv: `source venv/bin/activate` |

## Next Steps

- Review workflow configuration in `config/sdlc_config.yaml`
- Set up required Confluence pages for your project
- Test with a real task from your backlog
