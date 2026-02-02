# Quick Start Guide

Get up and running with AI SDLC Executor in 10 minutes.

## Prerequisites

- Python 3.11+
- Jira Cloud account with API access
- Confluence Cloud account with API access
- Anthropic API key (for Claude)

## Step 1: Setup Virtual Environment

**⚠️ IMPORTANT**: Always use a virtual environment to isolate dependencies!

### Option A: Automated Setup (Recommended)

```bash
# Run setup script (creates venv + installs dependencies)
./setup.sh
```

The script will:
- ✅ Create virtual environment (`venv/`)
- ✅ Activate it
- ✅ Install all dependencies
- ✅ Show next steps

### Option B: Manual Setup

```bash
# 1. Create virtual environment
python3 -m venv venv

# 2. Activate it
source venv/bin/activate  # On macOS/Linux
# OR
venv\Scripts\activate     # On Windows

# 3. Upgrade pip
pip install --upgrade pip

# 4. Install dependencies
pip install -r requirements.txt
```

**Verify activation**:
```bash
# Should show path to venv/bin/python
which python

# Should be 3.11+
python --version
```

### Alternative: Using Poetry

```bash
# Install Poetry first: https://python-poetry.org/docs/#installation
poetry install
poetry shell  # Activates venv
```

## Step 2: Install Dependencies

If you used `./setup.sh`, dependencies are already installed. Skip to Step 3!

If manual setup:
```bash
# Make sure venv is activated first!
pip install -r requirements.txt
```

## Step 3: Configure Credentials

### ⚠️ IMPORTANT: Create Bot Account First!

**Do NOT use your personal Atlassian account!**

The executor agent must use a dedicated bot account for:
- ✅ Clear audit trail (shows "AI Executor Bot" not your name)
- ✅ Security (separate credentials)
- ✅ SDLC compliance (agent identity must be visible)

**📖 [Follow the Bot Account Setup Guide](docs/BOT_ACCOUNT_SETUP.md) first!**

This takes ~10 minutes:
1. Create bot email: `ai-executor-bot@yourcompany.com`
2. Invite to Atlassian workspace
3. Generate API token (as bot, not you!)
4. Grant permissions

### Configure Environment

Once bot account is ready:

```bash
# Copy example environment file
cp .env.example .env

# Edit .env with BOT credentials (not yours!)
nano .env
```

**Required credentials:**

1. **Bot's Jira API Token**:
   - ⚠️ Log in AS THE BOT (not yourself!)
   - Go to: https://id.atlassian.com/manage-profile/security/api-tokens
   - Click "Create API token"
   - Copy the token to `.env`

2. **Bot's Confluence API Token**:
   - Same token as Jira (Atlassian uses unified tokens)

3. **Anthropic API Key** (your personal or team key):
   - Go to: https://console.anthropic.com/settings/keys
   - Create a new API key
   - Copy to `.env`

**Example `.env` file:**

```env
# ⚠️ Use BOT account credentials, not your personal account!
JIRA_URL=https://yourcompany.atlassian.net
JIRA_EMAIL=ai-executor-bot@yourcompany.com
JIRA_API_TOKEN=your-api-token  # Bot's API token

CONFLUENCE_URL=https://yourcompany.atlassian.net/wiki
CONFLUENCE_EMAIL=ai-executor-bot@yourcompany.com
CONFLUENCE_API_TOKEN=your-api-token  # Same token

ANTHROPIC_API_KEY=your-anthropic-key  # Your/team API key
```

## Step 4: Verify Confluence Space

Ensure your Confluence space has the required structure:

1. **Space Home**: "🌌 AI Engineering Hub"
2. **SDLC Rules Page**: "SDLC & Workflows Rules"
3. **Product Registry**: Folder with projects

If these don't exist yet, create them manually in Confluence first.

## Step 5: Run Integration Tests

```bash
# Run all tests
./run_tests.sh

# Or test a specific Jira issue
./run_tests.sh AI-123
```

**Expected output:**

```
🚀 AI SDLC Executor - Test Runner
==================================

Running MCP Integration Tests...

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

✅ All tests passed!
```

## Step 6: Troubleshooting

### Common Issues

#### 1. "SDLC Rules page not found"

**Solution**: Create the page in Confluence with exact title: "SDLC & Workflows Rules"

```bash
# Check what title Confluence is expecting
grep sdlc_rules_page_title config/sdlc_config.yaml
```

#### 2. "Jira connection failed: 401 Unauthorized"

**Solution**: Check your API token and email

```bash
# Test Jira authentication manually
curl -u your.email@company.com:YOUR_API_TOKEN \
  https://yourcompany.atlassian.net/rest/api/3/myself
```

#### 3. "Module 'mcp' not found"

**Solution**: Install MCP package

```bash
pip install mcp
# or
poetry add mcp
```

#### 4. "Confluence search returns 0 pages"

**Possible causes:**
- Page title doesn't match exactly (case-sensitive!)
- Space key is wrong
- Insufficient permissions

**Debug:**

```python
# Test Confluence search directly
python -c "
import os
from dotenv import load_dotenv
load_dotenv()

from executor.mcp.servers.confluence_server import ConfluenceAPIClient

client = ConfluenceAPIClient(
    os.getenv('CONFLUENCE_URL'),
    os.getenv('CONFLUENCE_EMAIL'),
    os.getenv('CONFLUENCE_API_TOKEN')
)

# Search all pages
results = client.search_pages('type=page', limit=10)
for page in results:
    print(f\"- {page['title']}\")
"
```

## Next Steps

Once tests pass:

1. **Review Configuration**: Check [config/sdlc_config.yaml](config/sdlc_config.yaml)
2. **Read SDLC Rules**: Understand the workflow protocol
3. **Test with Real Issue**: Try analyzing an actual Jira issue
4. **Run Executor** (coming soon): Execute workflow phases

## Manual Testing

### Test Jira Connection

```python
from executor.mcp.servers.jira_server import JiraAPIClient
import os
from dotenv import load_dotenv

load_dotenv()

client = JiraAPIClient(
    os.getenv('JIRA_URL'),
    os.getenv('JIRA_EMAIL'),
    os.getenv('JIRA_API_TOKEN')
)

# Get an issue
issue = client.get_issue('AI-123')
print(f"Issue: {issue['key']} - {issue['fields']['summary']}")

# Search issues
issues = client.search_issues('status = "AI-TO-DO"', max_results=5)
print(f"Found {len(issues)} issues in AI-TO-DO")
```

### Test Confluence Connection

```python
from executor.mcp.servers.confluence_server import ConfluenceAPIClient
import os
from dotenv import load_dotenv

load_dotenv()

client = ConfluenceAPIClient(
    os.getenv('CONFLUENCE_URL'),
    os.getenv('CONFLUENCE_EMAIL'),
    os.getenv('CONFLUENCE_API_TOKEN')
)

# Search for SDLC page
results = client.search_pages('title = "SDLC & Workflows Rules"', limit=1)
if results:
    print(f"✓ Found SDLC Rules page: {results[0]['title']}")
else:
    print("✗ SDLC Rules page not found")
```

## Support

- **Issues**: Report bugs at [GitHub Issues](https://github.com/yourcompany/ai-sdlc-executor/issues)
- **Documentation**: See [README.md](README.md) for full documentation
- **SDLC Rules**: Review Confluence page "SDLC & Workflows Rules"

## What's Next?

Current implementation status:

- ✅ Custom MCP servers (Confluence & Jira)
- ✅ Data cleaning & validation
- ✅ Integration tests
- 🚧 State machine & phase handlers (in progress)
- 🚧 CLI interface (planned)

Stay tuned for the full Executor Agent implementation!
