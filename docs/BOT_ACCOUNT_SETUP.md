# Bot Account Setup Guide

This guide explains how to create a dedicated bot account for the AI SDLC Executor.

## Why Use a Bot Account?

✅ **Audit Trail**: All changes clearly attributed to "AI Executor Bot" not a human
✅ **Security**: Separate bot credentials from personal account
✅ **Compliance**: SDLC protocol requires agent identity to be visible
✅ **Permissions**: Bot account can have specific, limited permissions
✅ **Tracking**: Easy to distinguish agent actions from human actions

## Step-by-Step Setup

### 1. Create Bot Email Address

You need a dedicated email for the bot account.

**Option A: Create new email account** (Recommended)
```
ai-executor-bot@yourcompany.com
sdlc-bot@yourcompany.com
```

**Option B: Use email alias** (Quick solution)
```
yourname+ai-executor@company.com
yourname+sdlc-bot@company.com
```

> **Note**: Email aliases work with Gmail, Outlook, etc. (anything after `+` is ignored for delivery but creates unique address)

### 2. Create Atlassian Account

1. **Go to Atlassian Admin**:
   - Visit: https://admin.atlassian.com
   - Log in with your admin account

2. **Invite Bot User**:
   - Click "Users" in left sidebar
   - Click "Invite users" button
   - Enter bot email: `ai-executor-bot@yourcompany.com`
   - Select products:
     - ✅ Jira Software
     - ✅ Confluence
   - Click "Invite users"

3. **Accept Invitation** (from bot email):
   - Check the bot's email inbox
   - Open invitation email from Atlassian
   - Click "Join now" or acceptance link
   - Create account with:
     - **Full name**: "AI Executor Bot" or "SDLC Bot"
     - **Display name**: "🤖 AI Executor" (emoji optional but helps visibility)
     - **Password**: Create a strong password (save it securely!)

4. **Verify Account**:
   - Complete email verification if required
   - Log in to confirm account is active

### 3. Generate API Token

**CRITICAL**: Do this while logged in as the BOT account, not your personal account!

1. **Log in as bot**:
   - Use bot email and password
   - Go to: https://id.atlassian.com/manage-profile/security/api-tokens

2. **Create token**:
   - Click "Create API token"
   - Label: "SDLC Executor Agent"
   - Click "Create"
   - **COPY THE TOKEN IMMEDIATELY** (shown only once!)

3. **Save token securely**:
   - Store in password manager
   - Will use in `.env` file

### 4. Configure Bot Permissions

#### Jira Permissions

1. **Add to Project**:
   - Go to your Jira project
   - Settings → People
   - Add user: `ai-executor-bot@yourcompany.com`

2. **Grant Permissions** (Project Settings → Permissions):
   - ✅ **Browse projects**: View issues
   - ✅ **Create issues**: Create Stories/Tasks
   - ✅ **Edit issues**: Update descriptions
   - ✅ **Add comments**: Add analysis comments
   - ✅ **Transition issues**: Move through workflow
   - ❌ **Delete issues**: NOT needed (security)
   - ❌ **Administer projects**: NOT needed

3. **Test Access**:
   ```bash
   # Test with curl (use bot credentials)
   curl -u ai-executor-bot@yourcompany.com:BOT_API_TOKEN \
     https://yourcompany.atlassian.net/rest/api/3/myself

   # Should return bot user details
   ```

#### Confluence Permissions

1. **Add to Space**:
   - Go to Confluence space: "🌌 AI Engineering Hub"
   - Settings → Space permissions
   - Add user: `ai-executor-bot@yourcompany.com`

2. **Grant Permissions**:
   - ✅ **View**: View all pages
   - ✅ **Add**: Create new pages
   - ✅ **Edit**: Update existing pages
   - ✅ **Comment**: Add comments (if needed)
   - ❌ **Delete**: NOT needed (security)
   - ❌ **Admin**: NOT needed

3. **Test Access**:
   ```bash
   # Test with curl
   curl -u ai-executor-bot@yourcompany.com:BOT_API_TOKEN \
     https://yourcompany.atlassian.net/wiki/rest/api/space

   # Should return list of accessible spaces
   ```

### 5. Update Environment Configuration

Create your `.env` file:

```bash
cp .env.example .env
nano .env  # or your preferred editor
```

Update with bot credentials:

```env
# Atlassian Bot Account
JIRA_URL=https://yourcompany.atlassian.net
JIRA_EMAIL=ai-executor-bot@yourcompany.com
JIRA_API_TOKEN=ATATT3xFfGF0abc123...  # Bot's API token

CONFLUENCE_URL=https://yourcompany.atlassian.net/wiki
CONFLUENCE_EMAIL=ai-executor-bot@yourcompany.com
CONFLUENCE_API_TOKEN=ATATT3xFfGF0abc123...  # Same token

# Anthropic (use your personal or team key)
ANTHROPIC_API_KEY=sk-ant-api03-xxx...

# GitHub (use bot's personal access token)
GITHUB_TOKEN=ghp_xxx...
```

### 6. Verify Bot Account

Run the integration test:

```bash
./run_tests.sh
```

**Expected output** should show bot identity:

```
Testing Jira Connection...
Fetching as user: AI Executor Bot (ai-executor-bot@yourcompany.com)
✓ Jira connection successful
```

**Check in Jira/Confluence**:
- View any page/issue history
- Bot actions should show: "🤖 AI Executor Bot" (not your name)

## Security Best Practices

### Protect Bot Credentials

- ✅ **Never commit** `.env` file to Git (already in `.gitignore`)
- ✅ **Store token** in password manager
- ✅ **Rotate token** every 90 days
- ✅ **Use minimal permissions** (principle of least privilege)
- ❌ **Don't share** bot token with multiple tools

### Monitor Bot Activity

1. **Jira Audit Log**:
   - Settings → System → Audit log
   - Filter by user: "AI Executor Bot"
   - Review actions regularly

2. **Confluence Audit Log**:
   - Settings → Audit log
   - Filter by user: "AI Executor Bot"
   - Check for unexpected changes

### Emergency: Revoke Access

If bot is compromised:

1. **Revoke API token**:
   - Log in as bot
   - https://id.atlassian.com/manage-profile/security/api-tokens
   - Click "Revoke" on token

2. **Remove permissions**:
   - Remove bot from Jira projects
   - Remove bot from Confluence spaces

3. **Create new token**:
   - Generate new token
   - Update `.env` file

## Licensing Considerations

**Important**: In Atlassian Cloud, bot accounts count against your user license limits.

**Options**:

1. **Use existing license**: If you have available seats
2. **Upgrade plan**: Add +1 user for bot
3. **Use service account**: Some plans allow service accounts
4. **Share with existing bot**: If you already have automation bots

**Cost estimate**:
- Jira Cloud Standard: ~$7.75/user/month
- Confluence Cloud Standard: ~$5.75/user/month
- **Total for bot**: ~$13.50/month

> **Tip**: For MVP/testing, you can temporarily use a personal account, but switch to bot before production!

## Troubleshooting

### "401 Unauthorized" errors

**Causes**:
- Wrong email address
- Wrong API token
- Token not generated from bot account

**Fix**:
```bash
# Test authentication
curl -u ai-executor-bot@yourcompany.com:YOUR_TOKEN \
  https://yourcompany.atlassian.net/rest/api/3/myself

# Should return bot user details, not your details!
```

### "403 Forbidden" errors

**Causes**:
- Bot doesn't have permissions in project/space
- Bot not added to project/space

**Fix**:
- Add bot to Jira project
- Add bot to Confluence space
- Grant necessary permissions

### Bot changes show your name instead

**Cause**: You're using your personal API token, not bot's token

**Fix**:
1. Delete current API token from `.env`
2. Log in AS THE BOT (not yourself!)
3. Generate new token from bot account
4. Update `.env` with bot token

### Email alias not working

**Cause**: Your email provider doesn't support `+` aliases

**Fix**:
- Create a real email account for bot
- Or use Gmail/Outlook which support aliases

## Alternative: Use Your Personal Account (NOT Recommended)

If you absolutely cannot create a bot account, you can temporarily use your personal account:

```env
JIRA_EMAIL=your.name@company.com
JIRA_API_TOKEN=your_personal_token
```

**Limitations**:
- ❌ Audit trail shows YOUR name (not agent's)
- ❌ Can't distinguish human vs agent actions
- ❌ Violates SDLC protocol (agent identity not visible)
- ❌ Security risk (mixing personal and bot access)

**Only use this for**:
- Initial testing
- Development environment
- Proof of concept

**Always switch to bot account before**:
- Production deployment
- Shared team usage
- SDLC compliance audits

## Questions?

- **Can bot account use SSO?** Yes, if your organization uses SSO
- **Can multiple people use same bot?** Yes, but each should have their own `.env` file
- **Can bot create Jira issues?** Yes, if granted "Create issues" permission
- **Can bot delete pages?** Only if granted delete permission (not recommended)
- **Does bot need admin access?** No! Use minimal permissions

---

**Next Step**: Once bot account is set up, run `./run_tests.sh` to verify everything works!
