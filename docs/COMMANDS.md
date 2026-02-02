# Command Reference

Quick reference for common commands.

## Initial Setup

```bash
# 1. Clone/navigate to project
cd AI-swarm

# 2. Run automated setup (recommended)
./setup.sh

# This creates venv + installs dependencies
# Follow the prompts
```

## Virtual Environment

### Activate venv

```bash
# macOS/Linux
source venv/bin/activate

# Windows
venv\Scripts\activate
```

**Verify activation**:
```bash
which python
# Should show: /path/to/AI-swarm/venv/bin/python

python --version
# Should show: Python 3.11.x or higher
```

### Deactivate venv

```bash
deactivate
```

### Recreate venv (if corrupted)

```bash
rm -rf venv
./setup.sh
```

## Configuration

### Create .env file

```bash
# Copy template
cp .env.example .env

# Edit with bot credentials
nano .env  # or code .env, vim .env, etc.
```

### Verify .env

```bash
# Check required variables are set
cat .env | grep -E "JIRA_EMAIL|CONFLUENCE_EMAIL|ANTHROPIC_API_KEY"
```

## Testing

### Run all tests

```bash
# Make sure venv is activated first!
source venv/bin/activate

# Run tests
./run_tests.sh
```

### Test specific Jira issue

```bash
./run_tests.sh AI-123
```

### Test Jira connection manually

```bash
python -c "
import os
from dotenv import load_dotenv
load_dotenv()

from executor.mcp.servers.jira_server import JiraAPIClient

client = JiraAPIClient(
    os.getenv('JIRA_URL'),
    os.getenv('JIRA_EMAIL'),
    os.getenv('JIRA_API_TOKEN')
)

# Get current user (should show bot)
import requests
response = requests.get(
    f\"{os.getenv('JIRA_URL')}/rest/api/3/myself\",
    auth=(os.getenv('JIRA_EMAIL'), os.getenv('JIRA_API_TOKEN'))
)
print(response.json())
"
```

### Test Confluence connection manually

```bash
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

# Search for SDLC page
results = client.search_pages('title = \"SDLC & Workflows Rules\"', limit=1)
if results:
    print(f\"✅ Found: {results[0]['title']}\")
else:
    print(\"❌ SDLC Rules page not found\")
"
```

## Development

### Install new dependency

```bash
# Activate venv first
source venv/bin/activate

# Install package
pip install package-name

# Update requirements.txt
pip freeze > requirements.txt
```

### Run Python scripts

```bash
# Always activate venv first!
source venv/bin/activate

# Then run scripts
python your_script.py
```

### Format code (Black)

```bash
source venv/bin/activate
black src/ tests/
```

### Lint code (Ruff)

```bash
source venv/bin/activate
ruff check src/ tests/
```

### Type check (MyPy)

```bash
source venv/bin/activate
mypy src/
```

## Troubleshooting

### "Command not found: orchestrator"

**Cause**: venv not activated or package not installed

**Fix**:
```bash
source venv/bin/activate
pip install -e .
```

### "ModuleNotFoundError: No module named 'pydantic'"

**Cause**: Dependencies not installed or wrong Python

**Fix**:
```bash
source venv/bin/activate
pip install -r requirements.txt
```

### "python: command not found"

**Cause**: Python not installed

**Fix**:
```bash
# macOS
brew install python@3.11

# Ubuntu
sudo apt install python3.11
```

### Check which Python is being used

```bash
which python
# Should show venv path if activated

python --version
# Should be 3.11+

pip list
# Should show installed packages
```

## Quick Checklist

Before running anything:

- [ ] `cd AI-swarm` (in project directory)
- [ ] `source venv/bin/activate` (venv activated)
- [ ] `which python` shows venv path
- [ ] `.env` file exists with bot credentials
- [ ] Run `./run_tests.sh` to verify setup

## Common Workflows

### Daily Development Workflow

```bash
# 1. Navigate to project
cd AI-swarm

# 2. Activate venv
source venv/bin/activate

# 3. Pull latest changes (if using git)
git pull

# 4. Update dependencies if needed
pip install -r requirements.txt

# 5. Run tests
./run_tests.sh

# 6. Do your work...
python your_script.py

# 7. Deactivate when done
deactivate
```

### First-Time Setup Workflow

```bash
# 1. Clone/download project
cd AI-swarm

# 2. Run setup script
./setup.sh

# 3. Create bot account (see docs/BOT_ACCOUNT_SETUP.md)
# ... follow guide ...

# 4. Configure .env
cp .env.example .env
nano .env  # Add bot credentials

# 5. Test connection
./run_tests.sh

# 6. If tests pass, you're ready!
```

## Environment Variables Reference

| Variable | Description | Example |
|----------|-------------|---------|
| `JIRA_URL` | Jira base URL | `https://company.atlassian.net` |
| `JIRA_EMAIL` | Bot's email | `ai-executor-bot@company.com` |
| `JIRA_API_TOKEN` | Bot's API token | `your-api-token` |
| `CONFLUENCE_URL` | Confluence base URL | `https://company.atlassian.net/wiki` |
| `CONFLUENCE_EMAIL` | Bot's email (same) | `ai-executor-bot@company.com` |
| `CONFLUENCE_API_TOKEN` | Bot's API token (same) | `your-api-token` |
| `ANTHROPIC_API_KEY` | Claude API key | `your-anthropic-key` |
| `GITHUB_TOKEN` | GitHub PAT (optional) | `ghp_...` |

## Useful Shell Aliases

Add these to your `~/.bashrc` or `~/.zshrc`:

```bash
# AI SDLC Executor aliases
alias sdlc='cd /path/to/AI-swarm && source venv/bin/activate'
alias sdlc-test='cd /path/to/AI-swarm && source venv/bin/activate && ./run_tests.sh'
alias sdlc-setup='cd /path/to/AI-swarm && ./setup.sh'
```

Then you can just run:
```bash
sdlc        # Navigate and activate
sdlc-test   # Run tests
sdlc-setup  # Re-run setup
```

---

**Questions?** See [QUICKSTART.md](../QUICKSTART.md) or [BOT_ACCOUNT_SETUP.md](BOT_ACCOUNT_SETUP.md)
