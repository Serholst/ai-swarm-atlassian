# Bot Account Setup

How to create a dedicated bot account for the AI-Swarm executor.

## Why Use a Bot Account?

- **Audit Trail**: Changes show "AI Bot" not your personal name
- **Security**: Separate credentials from personal account
- **Compliance**: Agent identity is visible in all systems
- **Permissions**: Bot can have limited, specific access

## Setup Steps

### 1. Create Bot Email

Create a dedicated email for the bot:
```
ai-executor-bot@yourcompany.com
```

Or use an email alias:
```
yourname+ai-bot@company.com
```

### 2. Create Atlassian Account

1. Go to https://admin.atlassian.com
2. Click "Users" → "Invite users"
3. Enter bot email
4. Select products: Jira, Confluence
5. Accept invitation from bot's email inbox
6. Set display name: "AI Executor Bot"

### 3. Generate API Token

**Important**: Log in as the bot account, not your personal account!

1. Go to: https://id.atlassian.com/manage-profile/security/api-tokens
2. Click "Create API token"
3. Label: "SDLC Executor"
4. Copy token immediately (shown only once)

### 4. Configure Permissions

#### Jira

Add bot to project with permissions:
- ✅ Browse projects
- ✅ Create issues
- ✅ Edit issues
- ✅ Add comments
- ✅ Transition issues
- ❌ Delete issues (not needed)
- ❌ Administer (not needed)

#### Confluence

Add bot to space with permissions:
- ✅ View pages
- ✅ Add pages
- ✅ Edit pages
- ❌ Delete pages (not needed)
- ❌ Admin (not needed)

### 5. Update .env

```env
JIRA_URL=https://your-domain.atlassian.net
JIRA_EMAIL=ai-executor-bot@yourcompany.com
JIRA_API_TOKEN=<bot's token>

CONFLUENCE_URL=https://your-domain.atlassian.net
CONFLUENCE_EMAIL=ai-executor-bot@yourcompany.com
CONFLUENCE_API_TOKEN=<bot's token>
```

### 6. Verify

```bash
./run_tests.sh
```

Check that:
- Tests pass
- Jira comments show bot's name
- Confluence edits show bot's name

## Troubleshooting

| Error | Cause | Fix |
|-------|-------|-----|
| 401 Unauthorized | Wrong email/token | Verify credentials match |
| 403 Forbidden | No permissions | Add bot to project/space |
| Changes show your name | Using personal token | Generate token from bot account |

## Security

- Never commit `.env` to git
- Rotate token every 90 days
- Use minimal permissions
- Monitor bot activity in audit logs

## Cost

Bot account = +1 user license (~$13.50/month for Jira + Confluence Standard).

For testing, you can use a personal account temporarily, but switch to bot before production.
