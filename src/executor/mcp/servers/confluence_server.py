"""
Custom MCP Server for Confluence with data cleaning.

Purpose:
- Provide clean, validated Confluence data (prevent "garbage in, garbage out")
- Implement MCP protocol tools for Confluence operations
- Clean HTML to Markdown automatically

MCP Tools provided:
- confluence_get_page: Get a page by ID or title (cleaned)
- confluence_search_pages: Search pages with CQL
- confluence_get_space_home: Get space homepage
- confluence_get_project_passport: Get and parse Project Passport
- confluence_get_logical_architecture: Get and parse Logical Architecture
"""

import os
import logging
from typing import Any, Sequence
import requests
from requests.auth import HTTPBasicAuth

# MCP SDK imports
from mcp.server import Server
from mcp.types import Tool, TextContent
from mcp import stdio_server

# Local imports (models and utilities)
import sys
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent))

from executor.models.confluence_models import ConfluencePage, ConfluenceSpace
from executor.utils.html_cleaner import clean_confluence_html

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("confluence_mcp_server")


class ConfluenceAPIClient:
    """Confluence REST API client with cleaning."""

    def __init__(self, base_url: str, email: str, api_token: str):
        """
        Initialize Confluence client.

        Args:
            base_url: Confluence base URL (e.g., https://company.atlassian.net/wiki)
            email: User email
            api_token: API token
        """
        self.base_url = base_url.rstrip("/")
        self.auth = HTTPBasicAuth(email, api_token)
        self.session = requests.Session()
        self.session.auth = self.auth
        self.session.headers.update({"Accept": "application/json"})

    def get_page_by_id(self, page_id: str, expand: str = "body.storage,version,space") -> dict:
        """Get page by ID."""
        url = f"{self.base_url}/rest/api/content/{page_id}"
        params = {"expand": expand}

        response = self.session.get(url, params=params)
        response.raise_for_status()

        return response.json()

    def get_page_by_title(self, space_key: str, title: str) -> dict | None:
        """Get page by title in a space."""
        url = f"{self.base_url}/rest/api/content"
        params = {
            "spaceKey": space_key,
            "title": title,
            "expand": "body.storage,version,space",
        }

        response = self.session.get(url, params=params)
        response.raise_for_status()

        results = response.json().get("results", [])
        return results[0] if results else None

    def search_pages(self, cql: str, limit: int = 25) -> list[dict]:
        """Search pages using CQL."""
        url = f"{self.base_url}/rest/api/content/search"
        params = {
            "cql": cql,
            "limit": limit,
            "expand": "body.storage,version,space",
        }

        response = self.session.get(url, params=params)
        response.raise_for_status()

        return response.json().get("results", [])

    def get_space(self, space_key: str) -> dict:
        """Get space metadata."""
        url = f"{self.base_url}/rest/api/space/{space_key}"

        response = self.session.get(url)
        response.raise_for_status()

        return response.json()


# Initialize MCP Server
app = Server("confluence-mcp-server")

# Initialize Confluence client (from env vars)
CONFLUENCE_URL = os.getenv("CONFLUENCE_URL", "")
CONFLUENCE_EMAIL = os.getenv("CONFLUENCE_EMAIL", "")
CONFLUENCE_API_TOKEN = os.getenv("CONFLUENCE_API_TOKEN", "")

if not all([CONFLUENCE_URL, CONFLUENCE_EMAIL, CONFLUENCE_API_TOKEN]):
    logger.error("Missing required environment variables for Confluence")
    sys.exit(1)

confluence_client = ConfluenceAPIClient(CONFLUENCE_URL, CONFLUENCE_EMAIL, CONFLUENCE_API_TOKEN)


def _parse_confluence_page(page_data: dict) -> ConfluencePage:
    """
    Parse raw Confluence page data to cleaned ConfluencePage model.

    This performs the critical cleaning step.
    """
    # Extract body HTML
    body_html = page_data.get("body", {}).get("storage", {}).get("value", "")

    # Get space data
    space_data = page_data.get("space", {})
    space = ConfluenceSpace(
        key=space_data.get("key", ""),
        name=space_data.get("name", ""),
        id=str(space_data.get("id", "")),
    )

    # Get parent ID if exists
    ancestors = page_data.get("ancestors", [])
    parent_id = ancestors[-1]["id"] if ancestors else None

    # Get labels
    labels_data = page_data.get("metadata", {}).get("labels", {}).get("results", [])
    labels = [label.get("name", "") for label in labels_data]

    # Build URL
    page_url = f"{CONFLUENCE_URL}{page_data.get('_links', {}).get('webui', '')}"

    # Create cleaned page
    return ConfluencePage(
        id=page_data["id"],
        title=page_data["title"],
        space=space,
        status=page_data.get("status", "current"),
        body=body_html,  # Will be cleaned by field_validator
        version=page_data.get("version", {}).get("number", 1),
        created=page_data.get("history", {}).get("createdDate", ""),
        updated=page_data.get("version", {}).get("when", ""),
        url=page_url,
        labels=labels,
        parent_id=parent_id,
    )


# Define MCP Tools


@app.list_tools()
async def list_tools() -> list[Tool]:
    """List available Confluence tools."""
    return [
        Tool(
            name="confluence_get_page",
            description="Get a Confluence page by ID or title (returns cleaned Markdown)",
            inputSchema={
                "type": "object",
                "properties": {
                    "page_id": {
                        "type": "string",
                        "description": "Page ID (if known)",
                    },
                    "space_key": {
                        "type": "string",
                        "description": "Space key (required if using title)",
                    },
                    "title": {
                        "type": "string",
                        "description": "Page title (if using title instead of ID)",
                    },
                },
            },
        ),
        Tool(
            name="confluence_search_pages",
            description="Search Confluence pages using CQL",
            inputSchema={
                "type": "object",
                "properties": {
                    "cql": {
                        "type": "string",
                        "description": "Confluence Query Language (CQL) query",
                    },
                    "limit": {
                        "type": "number",
                        "description": "Max results (default 25)",
                        "default": 25,
                    },
                },
                "required": ["cql"],
            },
        ),
        Tool(
            name="confluence_get_space_home",
            description="Get the homepage of a Confluence space",
            inputSchema={
                "type": "object",
                "properties": {
                    "space_key": {
                        "type": "string",
                        "description": "Space key",
                    },
                },
                "required": ["space_key"],
            },
        ),
        Tool(
            name="confluence_get_project_passport",
            description="Get and parse a Project Passport page",
            inputSchema={
                "type": "object",
                "properties": {
                    "space_key": {
                        "type": "string",
                        "description": "Space key",
                    },
                    "project_name": {
                        "type": "string",
                        "description": "Project name (to find the page)",
                    },
                },
                "required": ["space_key", "project_name"],
            },
        ),
    ]


@app.call_tool()
async def call_tool(name: str, arguments: Any) -> Sequence[TextContent]:
    """Handle tool calls."""
    try:
        if name == "confluence_get_page":
            # Get page by ID or title
            page_id = arguments.get("page_id")
            space_key = arguments.get("space_key")
            title = arguments.get("title")

            if page_id:
                page_data = confluence_client.get_page_by_id(page_id)
            elif space_key and title:
                page_data = confluence_client.get_page_by_title(space_key, title)
                if not page_data:
                    return [TextContent(type="text", text=f"Page not found: {title}")]
            else:
                return [
                    TextContent(type="text", text="Either page_id or (space_key + title) required")
                ]

            # Parse and clean
            page = _parse_confluence_page(page_data)

            # Return cleaned content
            result = f"""# {page.title}

**Space:** {page.space.name} ({page.space.key})
**Version:** {page.version}
**URL:** {page.url}
**Labels:** {', '.join(page.labels)}

## Content (Cleaned Markdown)

{page.body}
"""
            return [TextContent(type="text", text=result)]

        elif name == "confluence_search_pages":
            cql = arguments["cql"]
            limit = arguments.get("limit", 25)

            results = confluence_client.search_pages(cql, limit=limit)

            # Parse results
            pages = [_parse_confluence_page(page_data) for page_data in results]

            # Format output
            output_lines = [f"Found {len(pages)} pages:\n"]
            for page in pages:
                output_lines.append(
                    f"- **{page.title}** ({page.space.key}) - [View]({page.url})\n"
                    f"  Version: {page.version}, Labels: {', '.join(page.labels)}"
                )

            return [TextContent(type="text", text="\n".join(output_lines))]

        elif name == "confluence_get_space_home":
            space_key = arguments["space_key"]

            # Get space
            space_data = confluence_client.get_space(space_key)

            # Get homepage
            homepage_id = space_data.get("homepage", {}).get("id")
            if not homepage_id:
                return [TextContent(type="text", text=f"Space {space_key} has no homepage")]

            page_data = confluence_client.get_page_by_id(homepage_id)
            page = _parse_confluence_page(page_data)

            result = f"""# {page.title} (Space Homepage)

**Space:** {page.space.name} ({page.space.key})
**URL:** {page.url}

## Content

{page.body}
"""
            return [TextContent(type="text", text=result)]

        elif name == "confluence_get_project_passport":
            space_key = arguments["space_key"]
            project_name = arguments["project_name"]

            # Search for Project Passport page
            cql = f'space = {space_key} AND title ~ "Project Passport" AND title ~ "{project_name}"'
            results = confluence_client.search_pages(cql, limit=1)

            if not results:
                return [
                    TextContent(
                        type="text",
                        text=f"Project Passport not found for: {project_name} in {space_key}",
                    )
                ]

            page_data = results[0]
            page = _parse_confluence_page(page_data)

            result = f"""# Project Passport: {project_name}

**Page ID:** {page.id}
**URL:** {page.url}
**Version:** {page.version}

## Content

{page.body}
"""
            return [TextContent(type="text", text=result)]

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
