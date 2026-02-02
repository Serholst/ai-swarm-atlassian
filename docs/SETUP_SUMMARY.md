# Setup Summary: Bot Account Configuration

## ✅ What We've Built

1. **Custom MCP Servers** with data cleaning (Confluence + Jira)
2. **Pydantic Data Models** for validated, clean data
3. **Integration Test Suite** to verify connections
4. **Complete Documentation** including bot account setup

## 🤖 Why Bot Account Matters

### SDLC Compliance

According to your SDLC template:

> **Executor (AI Agent) Actions**: All agent-generated content must be traceable and attributed to the agent, not a human operator.

**With Bot Account**:
```
Jira Comment by: 🤖 AI Executor Bot
Confluence Page updated by: AI Executor Bot
Git commits: Co-authored-by: AI Executor Bot <ai-executor-bot@company.com>
```

**Without Bot Account** (using your personal account):
```
Jira Comment by: Your Name  ❌ Violates SDLC!
Confluence Page updated by: Your Name  ❌ Can't distinguish human vs agent!
```

### Audit Trail Example

**Scenario**: Agent updates a Jira issue with architectural recommendations

**With Bot Account**:
1. Human creates Feature: "AI-123: Add OAuth2"
2. Bot adds comment: "[DRAFT] Architecture recommendations..." ← clearly shows agent
3. Human reviews and approves
4. Bot publishes to Confluence ← clearly shows agent
5. Human can see: "I made the ticket, agent analyzed it, I approved it"

**Without Bot Account**:
1. Your name creates Feature: "AI-123: Add OAuth2"
2. Your name adds comment: "[DRAFT] Architecture..." ← looks like you wrote it!
3. Your name publishes to Confluence ← looks like you did everything!
4. **Problem**: No one can tell what you did vs what the agent did!

## 📋 Setup Checklist

### Step 1: Create Bot Account (10 minutes)

See detailed guide: [BOT_ACCOUNT_SETUP.md](BOT_ACCOUNT_SETUP.md)

- [ ] Create bot email: `ai-executor-bot@yourcompany.com`
- [ ] Invite to Atlassian workspace
- [ ] Set display name: "🤖 AI Executor Bot"
- [ ] Generate API token (⚠️ while logged in AS BOT!)
- [ ] Add bot to Jira project with permissions:
  - ✅ Browse, Create, Edit issues
  - ✅ Add comments, Transition issues
- [ ] Add bot to Confluence space with permissions:
  - ✅ View, Add, Edit pages

### Step 2: Configure Environment

- [ ] Copy `.env.example` to `.env`
- [ ] Update with BOT credentials:
  ```env
  JIRA_EMAIL=ai-executor-bot@yourcompany.com
  JIRA_API_TOKEN=<bot's token, not yours!>
  CONFLUENCE_EMAIL=ai-executor-bot@yourcompany.com
  CONFLUENCE_API_TOKEN=<same token>
  ```
- [ ] Add Anthropic API key (your personal or team key)

### Step 3: Test Connection

- [ ] Install dependencies: `pip install -r requirements.txt`
- [ ] Run tests: `./run_tests.sh`
- [ ] Verify output shows bot identity:
  ```
  ✓ Jira connection successful
  Authenticated as: AI Executor Bot
  ```

## 🔍 How to Verify Bot is Working

### Test 1: Check API Authentication

```bash
# This should return BOT user details, not yours!
curl -u ai-executor-bot@yourcompany.com:YOUR_BOT_TOKEN \
  https://yourcompany.atlassian.net/rest/api/3/myself

# Expected response:
{
  "displayName": "AI Executor Bot",  # ← Should show bot name!
  "emailAddress": "ai-executor-bot@yourcompany.com"
}
```

**If you see YOUR name instead of bot name**: You're using your personal token! Generate token from bot account.

### Test 2: Create Test Comment

```bash
# Run this to add a test comment
python tests/test_mcp_integration.py AI-123
```

Then check Jira issue AI-123:
- Comment author should show: "🤖 AI Executor Bot"
- **NOT** your name!

### Test 3: Check Confluence History

1. Update any Confluence page via the agent
2. View page history
3. "Last modified by" should show: "AI Executor Bot"

## 🚨 Common Mistakes

### ❌ Mistake 1: Using Personal Token

```env
# WRONG - This is YOUR token, not bot's!
JIRA_EMAIL=ai-executor-bot@yourcompany.com
JIRA_API_TOKEN=<your personal token>  # ❌ Generated from YOUR account
```

**Result**: All changes show YOUR name, not bot's name

**Fix**: Log in AS THE BOT, generate token from bot account

### ❌ Mistake 2: Wrong Email in .env

```env
# WRONG - Email doesn't match token owner
JIRA_EMAIL=ai-executor-bot@yourcompany.com
JIRA_API_TOKEN=<token from your personal account>  # ❌ Mismatch!
```

**Result**: Authentication fails (401 Unauthorized)

**Fix**: Email must match the account that generated the token

### ❌ Mistake 3: Bot Not Added to Project/Space

```env
# Correct credentials, but...
JIRA_EMAIL=ai-executor-bot@yourcompany.com
JIRA_API_TOKEN=<correct bot token>
```

**Result**: 403 Forbidden errors (bot can't access resources)

**Fix**:
- Add bot to Jira project: Settings → People
- Add bot to Confluence space: Settings → Space permissions

## 💡 Quick Troubleshooting

| Issue | Cause | Fix |
|-------|-------|-----|
| "401 Unauthorized" | Wrong email/token combination | Verify email matches token owner |
| "403 Forbidden" | Bot not in project/space | Add bot with permissions |
| Changes show YOUR name | Using your personal token | Generate token from bot account |
| "JIRA_EMAIL not set" | .env file missing | Copy .env.example to .env |
| Tests can't find bot | Bot account doesn't exist | Create bot account first |

## 📊 Cost Consideration

**Atlassian Cloud Pricing**:
- Bot account = +1 user license
- Jira Standard: ~$7.75/month
- Confluence Standard: ~$5.75/month
- **Total**: ~$13.50/month for bot

**Alternatives**:
- Use personal account for MVP/testing (switch to bot later)
- Check if your plan includes service accounts
- Some enterprise plans allow automation bots

## ✅ Final Checklist Before Production

Before deploying to production or sharing with team:

- [ ] Bot account created with proper display name
- [ ] Bot API token generated (not personal token)
- [ ] Bot added to all required Jira projects
- [ ] Bot added to all required Confluence spaces
- [ ] Minimal permissions granted (no admin access)
- [ ] `.env` uses bot credentials
- [ ] Integration tests pass: `./run_tests.sh`
- [ ] Test comment shows bot name in Jira
- [ ] Confluence edits show bot name in history
- [ ] Token stored securely (password manager)
- [ ] `.env` not committed to Git (in `.gitignore`)

## 🎯 Next Steps

Once bot account is working:

1. **Run Integration Tests**:
   ```bash
   ./run_tests.sh
   ```

2. **Test with Real Issue**:
   ```bash
   ./run_tests.sh AI-123  # Use your actual issue key
   ```

3. **Verify Bot Identity**:
   - Check Jira comment shows bot name
   - Check Confluence history shows bot name

4. **Proceed to Development**:
   - Start implementing workflow phases
   - All agent actions will be properly attributed!

---

**Questions?** See [BOT_ACCOUNT_SETUP.md](BOT_ACCOUNT_SETUP.md) for detailed guide.
