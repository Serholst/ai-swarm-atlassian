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
"""

import os
import re
import logging
import time
from typing import Any, Sequence
import requests
from requests.auth import HTTPBasicAuth

# MCP SDK imports
from mcp.server import Server
from mcp.types import Tool, TextContent
from mcp import stdio_server

# HTML processing
from bs4 import BeautifulSoup
import html2text

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("confluence_mcp_server")


class RateLimiter:
    """Simple token bucket rate limiter."""

    def __init__(self, requests_per_second: float = 10.0, burst_size: int = 20):
        self.rps = requests_per_second
        self.burst_size = burst_size
        self._tokens = float(burst_size)
        self._last_update = time.monotonic()

    def acquire(self) -> None:
        """Acquire a token, blocking if necessary."""
        now = time.monotonic()
        elapsed = now - self._last_update
        self._tokens = min(self.burst_size, self._tokens + elapsed * self.rps)
        self._last_update = now

        if self._tokens < 1.0:
            wait_time = (1.0 - self._tokens) / self.rps
            logger.debug(f"Rate limit: waiting {wait_time:.2f}s")
            time.sleep(wait_time)
            self._tokens = 0.0
        else:
            self._tokens -= 1.0


def clean_confluence_html(html_content: str) -> str:
    """
    Clean Confluence HTML storage format to clean Markdown.

    Confluence uses custom HTML tags and macros. This function:
    1. Parses HTML with BeautifulSoup
    2. Removes Confluence-specific macros
    3. Converts to Markdown using html2text
    4. Cleans up whitespace and formatting
    """
    if not html_content or html_content.strip() == "":
        return ""

    soup = BeautifulSoup(html_content, "html.parser")

    # Remove Confluence macros
    for macro in soup.find_all("ac:structured-macro"):
        macro_name = macro.get("ac:name", "")
        if macro_name == "code":
            code_body = macro.find("ac:plain-text-body")
            if code_body:
                code_tag = soup.new_tag("pre")
                code_tag.string = code_body.get_text()
                macro.replace_with(code_tag)
        elif macro_name in ["panel", "info", "note", "warning"]:
            rich_text = macro.find("ac:rich-text-body")
            if rich_text:
                blockquote = soup.new_tag("blockquote")
                blockquote.string = rich_text.get_text()
                macro.replace_with(blockquote)
        else:
            macro.decompose()

    for tag in soup.find_all(["ac:parameter", "ac:link", "ac:image"]):
        tag.decompose()

    # Clean tables
    for table in soup.find_all("table"):
        table.attrs = {}

    # Convert Confluence links
    for link in soup.find_all("ri:page"):
        title = link.get("ri:content-title", "")
        if title:
            a_tag = soup.new_tag("a", href=f"#{title}")
            a_tag.string = title
            link.replace_with(a_tag)

    # Convert to Markdown
    h = html2text.HTML2Text()
    h.ignore_links = False
    h.ignore_images = False
    h.ignore_emphasis = False
    h.body_width = 0
    h.unicode_snob = True
    h.skip_internal_links = True

    markdown = h.handle(str(soup))

    # Cleanup
    markdown = re.sub(r"\n{3,}", "\n\n", markdown)
    markdown = "\n".join(line.rstrip() for line in markdown.split("\n"))
    markdown = re.sub(r"^(\s*)-\s+", r"\1- ", markdown, flags=re.MULTILINE)
    markdown = markdown.strip()

    return markdown


class ConfluenceAPIClient:
    """Confluence REST API client with cleaning and rate limiting."""

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
        self._rate_limiter = RateLimiter(requests_per_second=10.0, burst_size=20)

    def _request(self, method: str, url: str, **kwargs) -> requests.Response:
        """Make rate-limited request with retry."""
        max_retries = 3
        base_delay = 1.0

        for attempt in range(max_retries + 1):
            self._rate_limiter.acquire()

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

    def get_page_by_id(self, page_id: str, expand: str = "body.storage,version,space") -> dict:
        """Get page by ID."""
        url = f"{self.base_url}/rest/api/content/{page_id}"
        params = {"expand": expand}
        return self._request("GET", url, params=params).json()

    def get_page_by_title(self, space_key: str, title: str) -> dict | None:
        """Get page by title in a space."""
        url = f"{self.base_url}/rest/api/content"
        params = {
            "spaceKey": space_key,
            "title": title,
            "expand": "body.storage,version,space",
        }
        results = self._request("GET", url, params=params).json().get("results", [])
        return results[0] if results else None

    def search_pages(self, cql: str, limit: int = 25) -> list[dict]:
        """Search pages using CQL."""
        url = f"{self.base_url}/rest/api/content/search"
        params = {"cql": cql, "limit": limit, "expand": "body.storage,version,space"}
        return self._request("GET", url, params=params).json().get("results", [])

    def get_space(self, space_key: str) -> dict:
        """Get space metadata."""
        url = f"{self.base_url}/rest/api/space/{space_key}"
        return self._request("GET", url).json()


# Data models (inline to avoid sys.path issues)
from pydantic import BaseModel, Field, field_validator
from typing import Optional
from datetime import datetime


class ConfluenceSpace(BaseModel):
    key: str
    name: str
    id: str


class ConfluencePage(BaseModel):
    id: str
    title: str
    space: ConfluenceSpace
    status: str
    body: str = Field(..., description="Cleaned Markdown content")
    version: int
    created_at: datetime = Field(..., alias="created")
    updated_at: datetime = Field(..., alias="updated")
    url: str
    labels: list[str] = Field(default_factory=list)
    parent_id: Optional[str] = None

    class Config:
        populate_by_name = True

    @field_validator("body", mode="before")
    @classmethod
    def clean_body(cls, v: Any) -> str:
        if isinstance(v, dict):
            html_content = v.get("storage", {}).get("value", "")
        else:
            html_content = str(v)
        return clean_confluence_html(html_content)


# Initialize MCP Server
app = Server("confluence-mcp-server")

# Initialize Confluence client (from env vars)
CONFLUENCE_URL = os.getenv("CONFLUENCE_URL", "")
CONFLUENCE_EMAIL = os.getenv("CONFLUENCE_EMAIL", "")
CONFLUENCE_API_TOKEN = os.getenv("CONFLUENCE_API_TOKEN", "")

if not all([CONFLUENCE_URL, CONFLUENCE_EMAIL, CONFLUENCE_API_TOKEN]):
    logger.error("Missing required environment variables for Confluence")
    import sys
    sys.exit(1)

confluence_client = ConfluenceAPIClient(CONFLUENCE_URL, CONFLUENCE_EMAIL, CONFLUENCE_API_TOKEN)


def _parse_confluence_page(page_data: dict) -> ConfluencePage:
    """Parse raw Confluence page data to cleaned ConfluencePage model."""
    body_html = page_data.get("body", {}).get("storage", {}).get("value", "")

    space_data = page_data.get("space", {})
    space = ConfluenceSpace(
        key=space_data.get("key", ""),
        name=space_data.get("name", ""),
        id=str(space_data.get("id", "")),
    )

    ancestors = page_data.get("ancestors", [])
    parent_id = ancestors[-1]["id"] if ancestors else None

    labels_data = page_data.get("metadata", {}).get("labels", {}).get("results", [])
    labels = [label.get("name", "") for label in labels_data]

    page_url = f"{CONFLUENCE_URL}{page_data.get('_links', {}).get('webui', '')}"

    return ConfluencePage(
        id=page_data["id"],
        title=page_data["title"],
        space=space,
        status=page_data.get("status", "current"),
        body=body_html,
        version=page_data.get("version", {}).get("number", 1),
        created=page_data.get("history", {}).get("createdDate", datetime.now().isoformat()),
        updated=page_data.get("version", {}).get("when", datetime.now().isoformat()),
        url=page_url,
        labels=labels,
        parent_id=parent_id,
    )


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
                    "page_id": {"type": "string", "description": "Page ID (if known)"},
                    "space_key": {"type": "string", "description": "Space key (required if using title)"},
                    "title": {"type": "string", "description": "Page title (if using title instead of ID)"},
                },
            },
        ),
        Tool(
            name="confluence_search_pages",
            description="Search Confluence pages using CQL",
            inputSchema={
                "type": "object",
                "properties": {
                    "cql": {"type": "string", "description": "Confluence Query Language (CQL) query"},
                    "limit": {"type": "number", "description": "Max results (default 25)", "default": 25},
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
                    "space_key": {"type": "string", "description": "Space key"},
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
                    "space_key": {"type": "string", "description": "Space key"},
                    "project_name": {"type": "string", "description": "Project name (to find the page)"},
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
                return [TextContent(type="text", text="Either page_id or (space_key + title) required")]

            page = _parse_confluence_page(page_data)

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
            pages = [_parse_confluence_page(d) for d in results]

            output_lines = [f"Found {len(pages)} pages:\n"]
            for page in pages:
                output_lines.append(
                    f"- **{page.title}** ({page.space.key}) - [View]({page.url})\n"
                    f"  Version: {page.version}, Labels: {', '.join(page.labels)}"
                )
            return [TextContent(type="text", text="\n".join(output_lines))]

        elif name == "confluence_get_space_home":
            space_key = arguments["space_key"]
            space_data = confluence_client.get_space(space_key)

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

            cql = f'space = {space_key} AND title ~ "Project Passport" AND title ~ "{project_name}"'
            results = confluence_client.search_pages(cql, limit=1)

            if not results:
                return [TextContent(type="text", text=f"Project Passport not found for: {project_name} in {space_key}")]

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
