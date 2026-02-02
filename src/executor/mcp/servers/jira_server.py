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
import logging
from typing import Any, Sequence
import requests
from requests.auth import HTTPBasicAuth
from datetime import datetime

# MCP SDK imports
from mcp.server import Server
from mcp.types import Tool, TextContent
from mcp import stdio_server

# Local imports
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent))

from executor.models.jira_models import (
    JiraIssue,
    JiraProject,
    JiraStatus,
    JiraComment,
    JiraUser,
    JiraIssueType,
)

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("jira_mcp_server")


class JiraAPIClient:
    """Jira REST API client with cleaning."""

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
        self.session.headers.update({"Accept": "application/json", "Content-Type": "application/json"})

    def get_issue(self, issue_key: str) -> dict:
        """Get issue by key."""
        url = f"{self.base_url}/rest/api/3/issue/{issue_key}"
        params = {
            "expand": "renderedFields",
            "fields": "*all",
        }

        response = self.session.get(url, params=params)
        response.raise_for_status()

        return response.json()

    def search_issues(self, jql: str, max_results: int = 50) -> list[dict]:
        """Search issues with JQL."""
        url = f"{self.base_url}/rest/api/3/search"
        payload = {
            "jql": jql,
            "maxResults": max_results,
            "fields": "*all",
        }

        response = self.session.post(url, json=payload)
        response.raise_for_status()

        return response.json().get("issues", [])

    def get_comments(self, issue_key: str) -> list[dict]:
        """Get comments for an issue."""
        url = f"{self.base_url}/rest/api/3/issue/{issue_key}/comment"

        response = self.session.get(url)
        response.raise_for_status()

        return response.json().get("comments", [])

    def add_comment(self, issue_key: str, body: str) -> dict:
        """Add a comment to an issue."""
        url = f"{self.base_url}/rest/api/3/issue/{issue_key}/comment"

        # Convert Markdown to ADF (Atlassian Document Format)
        adf_body = self._markdown_to_adf(body)

        payload = {"body": adf_body}

        response = self.session.post(url, json=payload)
        response.raise_for_status()

        return response.json()

    def transition_issue(self, issue_key: str, transition_name: str) -> None:
        """Transition issue to a new status."""
        # Get available transitions
        url = f"{self.base_url}/rest/api/3/issue/{issue_key}/transitions"
        response = self.session.get(url)
        response.raise_for_status()

        transitions = response.json().get("transitions", [])

        # Find transition ID
        transition_id = None
        for trans in transitions:
            if trans["name"].lower() == transition_name.lower():
                transition_id = trans["id"]
                break

        if not transition_id:
            raise ValueError(f"Transition not found: {transition_name}")

        # Execute transition
        payload = {"transition": {"id": transition_id}}
        response = self.session.post(url, json=payload)
        response.raise_for_status()

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
            fields["description"] = self._markdown_to_adf(description)

        if parent_key:
            fields["parent"] = {"key": parent_key}

        payload = {"fields": fields}

        response = self.session.post(url, json=payload)
        response.raise_for_status()

        return response.json()

    @staticmethod
    def _markdown_to_adf(markdown: str) -> dict:
        """
        Convert simple Markdown to ADF.

        Note: This is a simplified converter. For production, use a proper library.
        """
        # Simple ADF structure for plain text
        paragraphs = markdown.split("\n\n")
        content = []

        for para in paragraphs:
            if para.strip():
                content.append({
                    "type": "paragraph",
                    "content": [{"type": "text", "text": para.strip()}],
                })

        return {"type": "doc", "version": 1, "content": content}


# Initialize MCP Server
app = Server("jira-mcp-server")

# Initialize Jira client
JIRA_URL = os.getenv("JIRA_URL", "")
JIRA_EMAIL = os.getenv("JIRA_EMAIL", "")
JIRA_API_TOKEN = os.getenv("JIRA_API_TOKEN", "")

if not all([JIRA_URL, JIRA_EMAIL, JIRA_API_TOKEN]):
    logger.error("Missing required environment variables for Jira")
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

    # Parse project
    project_data = fields.get("project", {})
    project = JiraProject(
        key=project_data.get("key", ""),
        name=project_data.get("name", ""),
        id=project_data.get("id", ""),
    )

    # Parse issue type
    issuetype_data = fields.get("issuetype", {})
    issue_type = JiraIssueType(
        id=issuetype_data.get("id", ""),
        name=issuetype_data.get("name", ""),
        hierarchical_level=issuetype_data.get("hierarchyLevel", 0),
    )

    # Parse status
    status_data = fields.get("status", {})
    status = JiraStatus(
        id=status_data.get("id", ""),
        name=status_data.get("name", ""),
        statusCategory=status_data.get("statusCategory", {}).get("name", ""),
    )

    # Parse users
    assignee = _parse_jira_user(fields.get("assignee"))
    reporter = _parse_jira_user(fields.get("reporter"))

    # Get parent key
    parent_data = fields.get("parent")
    parent_key = parent_data.get("key") if parent_data else None

    # Get subtasks
    subtasks = [subtask.get("key", "") for subtask in fields.get("subtasks", [])]

    # Parse dates
    created = fields.get("created", datetime.now().isoformat())
    updated = fields.get("updated", datetime.now().isoformat())

    return JiraIssue(
        key=issue_data["key"],
        id=issue_data["id"],
        self=issue_data.get("self", ""),
        project=project,
        issuetype=issue_type,
        summary=fields.get("summary", ""),
        description=fields.get("description"),  # Will be cleaned by validator
        status=status,
        assignee=assignee,
        reporter=reporter or JiraUser(account_id="unknown", display_name="Unknown"),
        labels=fields.get("labels", []),
        created=created,
        updated=updated,
        parent_key=parent_key,
        subtasks=subtasks,
    )


# Define MCP Tools


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
                    "issue_key": {
                        "type": "string",
                        "description": "Issue key (e.g., AI-123)",
                    },
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
                    "jql": {
                        "type": "string",
                        "description": "JQL query",
                    },
                    "max_results": {
                        "type": "number",
                        "description": "Max results (default 50)",
                        "default": 50,
                    },
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
                    "issue_key": {
                        "type": "string",
                        "description": "Issue key",
                    },
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
                    "issue_key": {
                        "type": "string",
                        "description": "Issue key",
                    },
                    "body": {
                        "type": "string",
                        "description": "Comment body (Markdown)",
                    },
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
                    "issue_key": {
                        "type": "string",
                        "description": "Issue key",
                    },
                    "transition_name": {
                        "type": "string",
                        "description": "Transition name (e.g., 'Analysis', 'In Progress')",
                    },
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
                    "project_key": {
                        "type": "string",
                        "description": "Project key",
                    },
                    "issue_type": {
                        "type": "string",
                        "description": "Issue type (Feature, Story, Task)",
                    },
                    "summary": {
                        "type": "string",
                        "description": "Issue summary/title",
                    },
                    "description": {
                        "type": "string",
                        "description": "Issue description (Markdown)",
                    },
                    "parent_key": {
                        "type": "string",
                        "description": "Parent issue key (for Stories)",
                    },
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
            issues = [_parse_jira_issue(issue_data) for issue_data in issues_data]

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

                # Extract text from ADF
                body_text = JiraComment._extract_adf_text(body) if isinstance(body, dict) else str(body)

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

            result = jira_client.create_issue(
                project_key, issue_type, summary, description, parent_key
            )

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
