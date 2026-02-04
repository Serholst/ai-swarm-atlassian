"""
Custom MCP Server for Jira with data cleaning.

Purpose:
- Provide clean, validated Jira data
- Implement MCP protocol tools for Jira operations
- Clean ADF (Atlassian Document Format) to Markdown

MCP Tools provided:
- jira_get_issue: Get an issue by key (cleaned)
- jira_search_issues: Search issues with JQL
- jira_get_comments: Get comments for an issue (cleaned)
- jira_add_comment: Add a comment to an issue
- jira_transition_issue: Change issue status
- jira_create_issue: Create a new issue
"""

import os
import re
import sys
import logging
import time
from typing import Any, Sequence
import requests
from requests.auth import HTTPBasicAuth
from datetime import datetime

# Add package root to path for model imports
# Need 4 levels up: servers -> mcp -> executor -> src
_package_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
if _package_root not in sys.path:
    sys.path.insert(0, _package_root)

# Import models from canonical location
from executor.models import JiraUser, JiraStatus, JiraIssueType, JiraProject, JiraIssue

# Import shared rate limiter
from executor.utils.rate_limiter import RateLimiter

# MCP SDK imports
from mcp.server import Server
from mcp.types import Tool, TextContent
from mcp import stdio_server

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("jira_mcp_server")


class MarkdownToADF:
    """Convert Markdown to Atlassian Document Format (ADF)."""

    @classmethod
    def convert(cls, markdown: str) -> dict:
        """
        Convert Markdown to ADF.

        Supports:
        - Paragraphs
        - Headers (h1-h6)
        - Bold, italic, strikethrough
        - Code blocks and inline code
        - Bullet and numbered lists
        - Links
        - Blockquotes
        """
        if not markdown or not markdown.strip():
            return {"type": "doc", "version": 1, "content": []}

        lines = markdown.split("\n")
        content = []
        i = 0

        while i < len(lines):
            line = lines[i]

            # Code block
            if line.startswith("```"):
                code_lines = []
                language = line[3:].strip() or None
                i += 1
                while i < len(lines) and not lines[i].startswith("```"):
                    code_lines.append(lines[i])
                    i += 1
                content.append(cls._code_block("\n".join(code_lines), language))
                i += 1
                continue

            # Header
            header_match = re.match(r"^(#{1,6})\s+(.+)$", line)
            if header_match:
                level = len(header_match.group(1))
                text = header_match.group(2)
                content.append(cls._heading(text, level))
                i += 1
                continue

            # Blockquote
            if line.startswith(">"):
                quote_lines = []
                while i < len(lines) and lines[i].startswith(">"):
                    quote_lines.append(lines[i][1:].strip())
                    i += 1
                content.append(cls._blockquote("\n".join(quote_lines)))
                continue

            # Bullet list
            if re.match(r"^[-*]\s+", line):
                items = []
                while i < len(lines) and re.match(r"^[-*]\s+", lines[i]):
                    items.append(re.sub(r"^[-*]\s+", "", lines[i]))
                    i += 1
                content.append(cls._bullet_list(items))
                continue

            # Numbered list
            if re.match(r"^\d+\.\s+", line):
                items = []
                while i < len(lines) and re.match(r"^\d+\.\s+", lines[i]):
                    items.append(re.sub(r"^\d+\.\s+", "", lines[i]))
                    i += 1
                content.append(cls._ordered_list(items))
                continue

            # Empty line (skip)
            if not line.strip():
                i += 1
                continue

            # Regular paragraph
            para_lines = [line]
            i += 1
            while i < len(lines) and lines[i].strip() and not cls._is_special_line(lines[i]):
                para_lines.append(lines[i])
                i += 1
            content.append(cls._paragraph(" ".join(para_lines)))

        return {"type": "doc", "version": 1, "content": content}

    @classmethod
    def _is_special_line(cls, line: str) -> bool:
        """Check if line starts a special block."""
        return (
            line.startswith("```")
            or line.startswith("#")
            or line.startswith(">")
            or re.match(r"^[-*]\s+", line)
            or re.match(r"^\d+\.\s+", line)
        )

    @classmethod
    def _paragraph(cls, text: str) -> dict:
        """Create paragraph node with inline formatting."""
        return {"type": "paragraph", "content": cls._parse_inline(text)}

    @classmethod
    def _heading(cls, text: str, level: int) -> dict:
        """Create heading node."""
        return {
            "type": "heading",
            "attrs": {"level": level},
            "content": cls._parse_inline(text),
        }

    @classmethod
    def _code_block(cls, code: str, language: str | None = None) -> dict:
        """Create code block node."""
        node = {
            "type": "codeBlock",
            "content": [{"type": "text", "text": code}],
        }
        if language:
            node["attrs"] = {"language": language}
        return node

    @classmethod
    def _blockquote(cls, text: str) -> dict:
        """Create blockquote node."""
        return {
            "type": "blockquote",
            "content": [cls._paragraph(text)],
        }

    @classmethod
    def _bullet_list(cls, items: list[str]) -> dict:
        """Create bullet list node."""
        return {
            "type": "bulletList",
            "content": [
                {"type": "listItem", "content": [cls._paragraph(item)]}
                for item in items
            ],
        }

    @classmethod
    def _ordered_list(cls, items: list[str]) -> dict:
        """Create ordered list node."""
        return {
            "type": "orderedList",
            "content": [
                {"type": "listItem", "content": [cls._paragraph(item)]}
                for item in items
            ],
        }

    @classmethod
    def _parse_inline(cls, text: str) -> list[dict]:
        """Parse inline formatting (bold, italic, code, links)."""
        if not text:
            return [{"type": "text", "text": ""}]

        result = []
        remaining = text

        while remaining:
            # Link: [text](url)
            link_match = re.match(r"\[([^\]]+)\]\(([^)]+)\)", remaining)
            if link_match:
                result.append({
                    "type": "text",
                    "text": link_match.group(1),
                    "marks": [{"type": "link", "attrs": {"href": link_match.group(2)}}],
                })
                remaining = remaining[link_match.end():]
                continue

            # Inline code: `code`
            code_match = re.match(r"`([^`]+)`", remaining)
            if code_match:
                result.append({
                    "type": "text",
                    "text": code_match.group(1),
                    "marks": [{"type": "code"}],
                })
                remaining = remaining[code_match.end():]
                continue

            # Bold: **text** or __text__
            bold_match = re.match(r"\*\*([^*]+)\*\*|__([^_]+)__", remaining)
            if bold_match:
                result.append({
                    "type": "text",
                    "text": bold_match.group(1) or bold_match.group(2),
                    "marks": [{"type": "strong"}],
                })
                remaining = remaining[bold_match.end():]
                continue

            # Italic: *text* or _text_
            italic_match = re.match(r"\*([^*]+)\*|_([^_]+)_", remaining)
            if italic_match:
                result.append({
                    "type": "text",
                    "text": italic_match.group(1) or italic_match.group(2),
                    "marks": [{"type": "em"}],
                })
                remaining = remaining[italic_match.end():]
                continue

            # Strikethrough: ~~text~~
            strike_match = re.match(r"~~([^~]+)~~", remaining)
            if strike_match:
                result.append({
                    "type": "text",
                    "text": strike_match.group(1),
                    "marks": [{"type": "strike"}],
                })
                remaining = remaining[strike_match.end():]
                continue

            # Plain text until next special char
            plain_match = re.match(r"[^[`*_~]+", remaining)
            if plain_match:
                result.append({"type": "text", "text": plain_match.group()})
                remaining = remaining[plain_match.end():]
                continue

            # Single special char (no match)
            result.append({"type": "text", "text": remaining[0]})
            remaining = remaining[1:]

        return result if result else [{"type": "text", "text": text}]


class JiraAPIClient:
    """Jira REST API client with cleaning and rate limiting."""

    def __init__(self, base_url: str, email: str, api_token: str):
        """
        Initialize Jira client.

        Args:
            base_url: Jira base URL (e.g., https://company.atlassian.net)
            email: User email
            api_token: API token
        """
        self.base_url = base_url.rstrip("/")
        self.auth = HTTPBasicAuth(email, api_token)
        self.session = requests.Session()
        self.session.auth = self.auth
        self.session.headers.update({
            "Accept": "application/json",
            "Content-Type": "application/json"
        })
        self._rate_limiter = RateLimiter(requests_per_second=10.0, burst_size=20)

    def _request(self, method: str, url: str, **kwargs) -> requests.Response:
        """Make rate-limited request with retry."""
        max_retries = 3
        base_delay = 1.0
        # Default timeout: 5s connect, 30s read
        kwargs.setdefault("timeout", (5, 30))

        for attempt in range(max_retries + 1):
            self._rate_limiter.acquire_sync()

            try:
                response = self.session.request(method, url, **kwargs)

                if response.status_code == 429:
                    if attempt < max_retries:
                        retry_after = int(response.headers.get("Retry-After", base_delay * (2 ** attempt)))
                        logger.warning(f"Rate limited (429), retrying in {retry_after}s")
                        time.sleep(retry_after)
                        continue
                    response.raise_for_status()

                response.raise_for_status()
                return response

            except requests.exceptions.RequestException as e:
                if attempt < max_retries and getattr(getattr(e, 'response', None), 'status_code', None) in (429, 503):
                    delay = base_delay * (2 ** attempt)
                    logger.warning(f"Request failed, retrying in {delay}s: {e}")
                    time.sleep(delay)
                    continue
                raise

        raise RuntimeError("Max retries exceeded")

    def get_issue(self, issue_key: str) -> dict:
        """Get issue by key."""
        url = f"{self.base_url}/rest/api/3/issue/{issue_key}"
        params = {"expand": "renderedFields", "fields": "*all"}
        return self._request("GET", url, params=params).json()

    def search_issues(self, jql: str, max_results: int = 50) -> list[dict]:
        """Search issues with JQL."""
        url = f"{self.base_url}/rest/api/3/search"
        payload = {"jql": jql, "maxResults": max_results, "fields": "*all"}
        return self._request("POST", url, json=payload).json().get("issues", [])

    def get_comments(self, issue_key: str) -> list[dict]:
        """Get comments for an issue."""
        url = f"{self.base_url}/rest/api/3/issue/{issue_key}/comment"
        return self._request("GET", url).json().get("comments", [])

    def add_comment(self, issue_key: str, body: str) -> dict:
        """Add a comment to an issue."""
        url = f"{self.base_url}/rest/api/3/issue/{issue_key}/comment"
        adf_body = MarkdownToADF.convert(body)
        payload = {"body": adf_body}
        return self._request("POST", url, json=payload).json()

    def transition_issue(self, issue_key: str, transition_name: str) -> None:
        """Transition issue to a new status."""
        url = f"{self.base_url}/rest/api/3/issue/{issue_key}/transitions"
        transitions = self._request("GET", url).json().get("transitions", [])

        transition_id = None
        for trans in transitions:
            if trans["name"].lower() == transition_name.lower():
                transition_id = trans["id"]
                break

        if not transition_id:
            raise ValueError(f"Transition not found: {transition_name}")

        payload = {"transition": {"id": transition_id}}
        self._request("POST", url, json=payload)

    def create_issue(
        self,
        project_key: str,
        issue_type: str,
        summary: str,
        description: str = "",
        parent_key: str | None = None,
    ) -> dict:
        """Create a new issue."""
        url = f"{self.base_url}/rest/api/3/issue"

        fields = {
            "project": {"key": project_key},
            "issuetype": {"name": issue_type},
            "summary": summary,
        }

        if description:
            fields["description"] = MarkdownToADF.convert(description)

        if parent_key:
            fields["parent"] = {"key": parent_key}

        payload = {"fields": fields}
        return self._request("POST", url, json=payload).json()


def extract_adf_text(adf: dict) -> str:
    """Extract plain text from Atlassian Document Format with basic formatting."""
    result = []

    def traverse(node: dict, list_prefix: str = "") -> str:
        node_type = node.get("type", "")
        content = node.get("content", [])

        if node_type == "text":
            return node.get("text", "")

        if node_type == "hardBreak":
            return "\n"

        if node_type == "paragraph":
            text = "".join(traverse(child) for child in content)
            return text + "\n"

        if node_type == "heading":
            level = node.get("attrs", {}).get("level", 1)
            text = "".join(traverse(child) for child in content)
            return "#" * level + " " + text + "\n"

        if node_type == "bulletList":
            items = []
            for child in content:
                items.append(traverse(child, "- "))
            return "".join(items)

        if node_type == "orderedList":
            items = []
            for i, child in enumerate(content, 1):
                items.append(traverse(child, f"{i}. "))
            return "".join(items)

        if node_type == "listItem":
            text = "".join(traverse(child) for child in content)
            return list_prefix + text

        if node_type == "codeBlock":
            text = "".join(traverse(child) for child in content)
            return f"```\n{text}```\n"

        if node_type == "blockquote":
            text = "".join(traverse(child) for child in content)
            lines = text.strip().split("\n")
            return "\n".join("> " + line for line in lines) + "\n"

        # Default: just traverse children
        return "".join(traverse(child) for child in content)

    if adf and isinstance(adf, dict):
        result = traverse(adf)
        # Clean up multiple blank lines
        result = re.sub(r"\n{3,}", "\n\n", result)
        return result.strip()
    return ""


# Initialize MCP Server
app = Server("jira-mcp-server")

# Initialize Jira client
JIRA_URL = os.getenv("JIRA_URL", "")
JIRA_EMAIL = os.getenv("JIRA_EMAIL", "")
JIRA_API_TOKEN = os.getenv("JIRA_API_TOKEN", "")

if not all([JIRA_URL, JIRA_EMAIL, JIRA_API_TOKEN]):
    logger.error("Missing required environment variables for Jira")
    import sys
    sys.exit(1)

jira_client = JiraAPIClient(JIRA_URL, JIRA_EMAIL, JIRA_API_TOKEN)


def _parse_jira_user(user_data: dict | None) -> JiraUser | None:
    """Parse Jira user data."""
    if not user_data:
        return None
    return JiraUser(
        account_id=user_data.get("accountId", ""),
        email=user_data.get("emailAddress"),
        display_name=user_data.get("displayName", "Unknown"),
    )


def _parse_jira_issue(issue_data: dict) -> JiraIssue:
    """Parse raw Jira issue data to cleaned JiraIssue model."""
    fields = issue_data.get("fields", {})

    project_data = fields.get("project", {})
    project = JiraProject(
        key=project_data.get("key", ""),
        name=project_data.get("name", ""),
        id=project_data.get("id", ""),
    )

    issuetype_data = fields.get("issuetype", {})
    issue_type = JiraIssueType(
        id=issuetype_data.get("id", ""),
        name=issuetype_data.get("name", ""),
        hierarchical_level=issuetype_data.get("hierarchyLevel", 0),
    )

    status_data = fields.get("status", {})
    status = JiraStatus(
        id=status_data.get("id", ""),
        name=status_data.get("name", ""),
        statusCategory=status_data.get("statusCategory", {}).get("name", ""),
    )

    assignee = _parse_jira_user(fields.get("assignee"))
    reporter = _parse_jira_user(fields.get("reporter"))

    parent_data = fields.get("parent")
    parent_key = parent_data.get("key") if parent_data else None

    subtasks = [subtask.get("key", "") for subtask in fields.get("subtasks", [])]

    created = fields.get("created")
    updated = fields.get("updated")
    if not created or not updated:
        issue_key = issue_data.get("key", "unknown")
        logger.warning(f"Issue {issue_key} missing timestamps: created={created}, updated={updated}")
        raise ValueError(f"Issue {issue_key} missing required timestamp fields")

    # Convert ADF description to plain text
    description_adf = fields.get("description")
    description_text = ""
    if description_adf and isinstance(description_adf, dict):
        description_text = extract_adf_text(description_adf)
    elif description_adf:
        description_text = str(description_adf)

    # Extract custom "Project" field (Confluence folder name)
    # customfield_10072 is a dropdown, customfield_10073 is text
    project_folder = ""
    cf_project_dropdown = fields.get("customfield_10072")
    if cf_project_dropdown and isinstance(cf_project_dropdown, dict):
        project_folder = cf_project_dropdown.get("value", "")
    if not project_folder:
        # Fallback to text field
        project_folder = fields.get("customfield_10073", "") or ""

    return JiraIssue(
        key=issue_data["key"],
        id=issue_data["id"],
        self=issue_data.get("self", ""),
        project=project,
        issuetype=issue_type,
        summary=fields.get("summary", ""),
        description=description_text,
        status=status,
        assignee=assignee,
        reporter=reporter or JiraUser(account_id="unknown", display_name="Unknown"),
        labels=fields.get("labels", []),
        created=created,
        updated=updated,
        parent_key=parent_key,
        subtasks=subtasks,
        project_folder=project_folder,
    )


@app.list_tools()
async def list_tools() -> list[Tool]:
    """List available Jira tools."""
    return [
        Tool(
            name="jira_get_issue",
            description="Get a Jira issue by key (returns cleaned data)",
            inputSchema={
                "type": "object",
                "properties": {
                    "issue_key": {"type": "string", "description": "Issue key (e.g., AI-123)"},
                },
                "required": ["issue_key"],
            },
        ),
        Tool(
            name="jira_search_issues",
            description="Search Jira issues using JQL",
            inputSchema={
                "type": "object",
                "properties": {
                    "jql": {"type": "string", "description": "JQL query"},
                    "max_results": {"type": "number", "description": "Max results (default 50)", "default": 50},
                },
                "required": ["jql"],
            },
        ),
        Tool(
            name="jira_get_comments",
            description="Get comments for a Jira issue (cleaned)",
            inputSchema={
                "type": "object",
                "properties": {
                    "issue_key": {"type": "string", "description": "Issue key"},
                },
                "required": ["issue_key"],
            },
        ),
        Tool(
            name="jira_add_comment",
            description="Add a comment to a Jira issue",
            inputSchema={
                "type": "object",
                "properties": {
                    "issue_key": {"type": "string", "description": "Issue key"},
                    "body": {"type": "string", "description": "Comment body (Markdown)"},
                },
                "required": ["issue_key", "body"],
            },
        ),
        Tool(
            name="jira_transition_issue",
            description="Change issue status/transition",
            inputSchema={
                "type": "object",
                "properties": {
                    "issue_key": {"type": "string", "description": "Issue key"},
                    "transition_name": {"type": "string", "description": "Transition name"},
                },
                "required": ["issue_key", "transition_name"],
            },
        ),
        Tool(
            name="jira_create_issue",
            description="Create a new Jira issue",
            inputSchema={
                "type": "object",
                "properties": {
                    "project_key": {"type": "string", "description": "Project key"},
                    "issue_type": {"type": "string", "description": "Issue type (Feature, Story, Task)"},
                    "summary": {"type": "string", "description": "Issue summary/title"},
                    "description": {"type": "string", "description": "Issue description (Markdown)"},
                    "parent_key": {"type": "string", "description": "Parent issue key (for Stories)"},
                },
                "required": ["project_key", "issue_type", "summary"],
            },
        ),
    ]


@app.call_tool()
async def call_tool(name: str, arguments: Any) -> Sequence[TextContent]:
    """Handle tool calls."""
    try:
        if name == "jira_get_issue":
            issue_key = arguments["issue_key"]
            issue_data = jira_client.get_issue(issue_key)
            issue = _parse_jira_issue(issue_data)

            result = f"""# {issue.key}: {issue.summary}

**Type:** {issue.issue_type.name}
**Status:** {issue.status.name}
**Project:** {issue.project.name} ({issue.project.key})
**Project Folder:** {issue.project_folder or 'None'}
**Assignee:** {issue.assignee.display_name if issue.assignee else 'Unassigned'}
**Labels:** {', '.join(issue.labels) if issue.labels else 'None'}

## Description

{issue.description or '[No description]'}

## Metadata

- Created: {issue.created}
- Updated: {issue.updated}
- Parent: {issue.parent_key or 'None'}
- Subtasks: {', '.join(issue.subtasks) if issue.subtasks else 'None'}
"""
            return [TextContent(type="text", text=result)]

        elif name == "jira_search_issues":
            jql = arguments["jql"]
            max_results = arguments.get("max_results", 50)
            issues_data = jira_client.search_issues(jql, max_results=max_results)
            issues = [_parse_jira_issue(d) for d in issues_data]

            output_lines = [f"Found {len(issues)} issues:\n"]
            for issue in issues:
                output_lines.append(
                    f"- **{issue.key}**: {issue.summary}\n"
                    f"  Status: {issue.status.name}, Type: {issue.issue_type.name}"
                )
            return [TextContent(type="text", text="\n".join(output_lines))]

        elif name == "jira_get_comments":
            issue_key = arguments["issue_key"]
            comments_data = jira_client.get_comments(issue_key)

            output_lines = [f"Comments for {issue_key}:\n"]
            for comment_data in comments_data:
                author = comment_data.get("author", {}).get("displayName", "Unknown")
                created = comment_data.get("created", "")
                body = comment_data.get("body", {})
                body_text = extract_adf_text(body) if isinstance(body, dict) else str(body)
                output_lines.append(f"\n### {author} - {created}\n\n{body_text}\n")

            return [TextContent(type="text", text="\n".join(output_lines))]

        elif name == "jira_add_comment":
            issue_key = arguments["issue_key"]
            body = arguments["body"]
            jira_client.add_comment(issue_key, body)
            return [TextContent(type="text", text=f"Comment added to {issue_key}")]

        elif name == "jira_transition_issue":
            issue_key = arguments["issue_key"]
            transition_name = arguments["transition_name"]
            jira_client.transition_issue(issue_key, transition_name)
            return [TextContent(type="text", text=f"Issue {issue_key} transitioned to {transition_name}")]

        elif name == "jira_create_issue":
            project_key = arguments["project_key"]
            issue_type = arguments["issue_type"]
            summary = arguments["summary"]
            description = arguments.get("description", "")
            parent_key = arguments.get("parent_key")

            result = jira_client.create_issue(project_key, issue_type, summary, description, parent_key)
            new_key = result.get("key", "")
            return [TextContent(type="text", text=f"Created issue: {new_key}")]

        else:
            return [TextContent(type="text", text=f"Unknown tool: {name}")]

    except Exception as e:
        logger.error(f"Error in tool {name}: {e}", exc_info=True)
        return [TextContent(type="text", text=f"Error: {str(e)}")]


async def main():
    """Run the MCP server."""
    async with stdio_server() as (read_stream, write_stream):
        await app.run(read_stream, write_stream, app.create_initialization_options())


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
