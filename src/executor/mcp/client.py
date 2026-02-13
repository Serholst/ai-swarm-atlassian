"""
MCP Client Manager for interacting with custom MCP servers.

This client wraps the MCP protocol and provides a simple interface
for the Executor Agent to interact with Jira, Confluence, and GitHub.

Features:
- Async I/O for non-blocking operations
- Unique request IDs for request/response correlation
- Proper process lifecycle management
- GitHub MCP via official @modelcontextprotocol/server-github
"""

import asyncio
import subprocess
import json
import logging
import shutil
from typing import Any
from pathlib import Path
import threading
from concurrent.futures import ThreadPoolExecutor

logger = logging.getLogger(__name__)


class RequestIDGenerator:
    """Thread-safe incrementing request ID generator."""

    def __init__(self):
        self._counter = 0
        self._lock = threading.Lock()

    def next(self) -> int:
        """Get next unique request ID."""
        with self._lock:
            self._counter += 1
            return self._counter


class MCPClient:
    """
    Client for interacting with a single MCP server.

    This client communicates with MCP servers via stdio with async support.
    Supports both Python scripts and external commands (e.g., npx for GitHub MCP).
    """

    def __init__(
        self,
        server_script: str | None = None,
        env: dict[str, str] | None = None,
        command: str | None = None,
        args: list[str] | None = None,
    ):
        """
        Initialize MCP client.

        Args:
            server_script: Path to MCP server script (Python)
            env: Environment variables for server
            command: External command to run (e.g., "npx")
            args: Arguments for external command
        """
        self.server_script = server_script
        self.command = command
        self.args = args or []
        self.env = env or {}
        self.process: subprocess.Popen | None = None
        self._id_generator = RequestIDGenerator()
        self._executor = ThreadPoolExecutor(max_workers=1)
        self._lock = threading.Lock()

    def start(self) -> None:
        """Start the MCP server process."""
        if self.process:
            logger.warning("Server already running")
            return

        import os
        import sys

        env = os.environ.copy()
        env.update(self.env)

        # Determine command to run
        if self.command:
            # External command (e.g., npx for GitHub MCP)
            cmd = [self.command] + self.args
            server_name = f"{self.command} {' '.join(self.args)}"
        else:
            # Python script (Jira, Confluence servers)
            cmd = [sys.executable, self.server_script]
            server_name = self.server_script

        self.process = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            text=True,
            bufsize=1,  # Line buffered
        )

        logger.info(f"Started MCP server: {server_name}")

        # Perform MCP initialization handshake
        self._initialize()

    def _initialize(self) -> None:
        """Perform MCP protocol initialization handshake."""
        if not self.process:
            raise RuntimeError("Server not started")

        # Send initialize request
        init_request = {
            "jsonrpc": "2.0",
            "id": self._id_generator.next(),
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {
                    "name": "ai-sdlc-executor",
                    "version": "0.1.0"
                }
            }
        }

        response = self._send_request(init_request)

        if "error" in response:
            raise RuntimeError(f"MCP initialization failed: {response['error']}")

        # Send initialized notification
        initialized_notification = {
            "jsonrpc": "2.0",
            "method": "notifications/initialized"
        }

        self._send_notification(initialized_notification)
        logger.info("MCP initialization handshake completed")

    def _send_request(self, request: dict) -> dict:
        """Send request and wait for response (thread-safe)."""
        with self._lock:
            if not self.process or not self.process.stdin or not self.process.stdout:
                raise RuntimeError("Server not available")

            request_json = json.dumps(request) + "\n"
            self.process.stdin.write(request_json)
            self.process.stdin.flush()

            response_line = self.process.stdout.readline()
            if not response_line:
                raise RuntimeError("No response from server")

            return json.loads(response_line)

    def _send_notification(self, notification: dict) -> None:
        """Send notification (no response expected)."""
        with self._lock:
            if not self.process or not self.process.stdin:
                raise RuntimeError("Server not available")

            notification_json = json.dumps(notification) + "\n"
            self.process.stdin.write(notification_json)
            self.process.stdin.flush()

    def stop(self) -> None:
        """Stop the MCP server process."""
        if self.process:
            try:
                self.process.terminate()
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
            finally:
                self.process = None
                logger.info(f"Stopped MCP server: {self.server_script}")

    def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> str:
        """
        Call a tool on the MCP server (synchronous).

        Args:
            tool_name: Name of the tool
            arguments: Tool arguments

        Returns:
            Tool result as string
        """
        if not self.process:
            raise RuntimeError("Server not started")

        request = {
            "jsonrpc": "2.0",
            "id": self._id_generator.next(),
            "method": "tools/call",
            "params": {
                "name": tool_name,
                "arguments": arguments,
            },
        }

        response = self._send_request(request)

        if "error" in response:
            raise RuntimeError(f"MCP error: {response['error']}")

        result = response.get("result", {})
        content = result.get("content", [])

        if content and len(content) > 0:
            return content[0].get("text", "")

        return ""

    async def call_tool_async(self, tool_name: str, arguments: dict[str, Any]) -> str:
        """
        Call a tool on the MCP server (asynchronous).

        Args:
            tool_name: Name of the tool
            arguments: Tool arguments

        Returns:
            Tool result as string
        """
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            self._executor,
            self.call_tool,
            tool_name,
            arguments
        )

    def __enter__(self):
        """Context manager entry."""
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit."""
        self.stop()


class MCPClientManager:
    """
    Manager for multiple MCP clients (Jira, Confluence, GitHub).

    This provides a unified interface for the Executor Agent.
    """

    def __init__(self, config_path: str | Path = "config/sdlc_config.yaml"):
        """
        Initialize MCP client manager.

        Args:
            config_path: Path to SDLC config
        """
        self.config_path = Path(config_path)
        self.clients: dict[str, MCPClient] = {}

        # Find MCP server scripts
        self.server_dir = Path(__file__).parent / "servers"

    def start_all(self, env_vars: dict[str, str]) -> None:
        """
        Start all MCP servers.

        Args:
            env_vars: Environment variables (API tokens, etc.)
        """
        # Start Jira server (uses shared Atlassian credentials)
        jira_server = self.server_dir / "jira_server.py"
        jira_env = {
            "ATLASSIAN_URL": env_vars.get("ATLASSIAN_URL", ""),
            "ATLASSIAN_EMAIL": env_vars.get("ATLASSIAN_EMAIL", ""),
            "ATLASSIAN_API_TOKEN": env_vars.get("ATLASSIAN_API_TOKEN", ""),
        }
        # Pass custom field IDs if configured (instance-specific)
        import os
        for field_var in ("JIRA_PROJECT_DROPDOWN_FIELD", "JIRA_PROJECT_TEXT_FIELD", "JIRA_PROJECT_LINK_FIELD"):
            value = os.getenv(field_var, "")
            if value:
                jira_env[field_var] = value
        self.clients["jira"] = MCPClient(
            server_script=str(jira_server),
            env=jira_env,
        )
        self.clients["jira"].start()

        # Start Confluence server (uses shared Atlassian credentials)
        # CONFLUENCE_URL should include /wiki suffix for Confluence Cloud
        confluence_server = self.server_dir / "confluence_server.py"
        self.clients["confluence"] = MCPClient(
            server_script=str(confluence_server),
            env={
                "ATLASSIAN_URL": env_vars.get("ATLASSIAN_URL", ""),
                "CONFLUENCE_URL": env_vars.get("CONFLUENCE_URL", ""),
                "ATLASSIAN_EMAIL": env_vars.get("ATLASSIAN_EMAIL", ""),
                "ATLASSIAN_API_TOKEN": env_vars.get("ATLASSIAN_API_TOKEN", ""),
            },
        )
        self.clients["confluence"].start()

        # Start GitHub server (official MCP server via npx)
        github_token = env_vars.get("GITHUB_TOKEN", "")
        if github_token:
            # Check if npx is available
            if shutil.which("npx"):
                self.clients["github"] = MCPClient(
                    command="npx",
                    args=["-y", "@modelcontextprotocol/server-github"],
                    env={"GITHUB_PERSONAL_ACCESS_TOKEN": github_token},
                )
                self.clients["github"].start()
                logger.info("GitHub MCP server started")
            else:
                logger.warning("npx not found - GitHub MCP server not started. Install Node.js to enable GitHub integration.")
        else:
            logger.info("GITHUB_TOKEN not set - GitHub MCP server not started")

        logger.info("All MCP servers started")

    def stop_all(self) -> None:
        """Stop all MCP servers."""
        for client in self.clients.values():
            client.stop()

        self.clients.clear()
        logger.info("All MCP servers stopped")

    # Synchronous methods (existing API)

    def jira_get_issue(self, issue_key: str) -> str:
        """Get Jira issue (cleaned)."""
        return self.clients["jira"].call_tool("jira_get_issue", {"issue_key": issue_key})

    def jira_search_issues(self, jql: str, max_results: int = 50) -> str:
        """Search Jira issues."""
        return self.clients["jira"].call_tool(
            "jira_search_issues", {"jql": jql, "max_results": max_results}
        )

    def jira_add_comment(self, issue_key: str, body: str) -> str:
        """Add comment to Jira issue."""
        return self.clients["jira"].call_tool(
            "jira_add_comment", {"issue_key": issue_key, "body": body}
        )

    def jira_transition_issue(self, issue_key: str, transition_name: str) -> str:
        """Transition Jira issue."""
        return self.clients["jira"].call_tool(
            "jira_transition_issue", {"issue_key": issue_key, "transition_name": transition_name}
        )

    def jira_get_comments(self, issue_key: str) -> str:
        """Get comments for a Jira issue."""
        return self.clients["jira"].call_tool(
            "jira_get_comments", {"issue_key": issue_key}
        )

    def jira_create_issue(
        self,
        project_key: str,
        issue_type: str,
        summary: str,
        description: str = "",
        parent_key: str | None = None,
    ) -> str:
        """
        Create a new Jira issue.

        Args:
            project_key: Jira project key
            issue_type: Issue type (Feature, Story, Task)
            summary: Issue title
            description: Issue description (Markdown)
            parent_key: Parent issue key (for Stories)

        Returns:
            Result message with created issue key
        """
        args: dict[str, Any] = {
            "project_key": project_key,
            "issue_type": issue_type,
            "summary": summary,
        }
        if description:
            args["description"] = description
        if parent_key:
            args["parent_key"] = parent_key

        return self.clients["jira"].call_tool("jira_create_issue", args)

    def jira_link_issues(
        self,
        from_key: str,
        to_key: str,
        link_type: str = "Blocks",
    ) -> str:
        """
        Link two Jira issues.

        Args:
            from_key: Issue creating the link (e.g., review task)
            to_key: Issue being linked to (e.g., original issue)
            link_type: Link type (Blocks, relates to, etc.)

        Returns:
            Result message
        """
        return self.clients["jira"].call_tool(
            "jira_link_issues",
            {"from_key": from_key, "to_key": to_key, "link_type": link_type}
        )

    def confluence_get_page(
        self, page_id: str | None = None, space_key: str | None = None, title: str | None = None
    ) -> str:
        """Get Confluence page (cleaned)."""
        args: dict[str, Any] = {}
        if page_id:
            args["page_id"] = page_id
        if space_key:
            args["space_key"] = space_key
        if title:
            args["title"] = title

        return self.clients["confluence"].call_tool("confluence_get_page", args)

    def confluence_search_pages(self, cql: str, limit: int = 25) -> str:
        """Search Confluence pages."""
        return self.clients["confluence"].call_tool(
            "confluence_search_pages", {"cql": cql, "limit": limit}
        )

    def confluence_get_space_home(self, space_key: str) -> str:
        """Get Confluence space homepage."""
        return self.clients["confluence"].call_tool(
            "confluence_get_space_home", {"space_key": space_key}
        )

    def confluence_get_page_ancestors(self, page_id: str) -> str:
        """Get Confluence page ancestors (parent chain)."""
        return self.clients["confluence"].call_tool(
            "confluence_get_page_ancestors", {"page_id": page_id}
        )

    # Async methods (new API)

    async def jira_get_issue_async(self, issue_key: str) -> str:
        """Get Jira issue asynchronously."""
        return await self.clients["jira"].call_tool_async(
            "jira_get_issue", {"issue_key": issue_key}
        )

    async def jira_search_issues_async(self, jql: str, max_results: int = 50) -> str:
        """Search Jira issues asynchronously."""
        return await self.clients["jira"].call_tool_async(
            "jira_search_issues", {"jql": jql, "max_results": max_results}
        )

    async def confluence_get_page_async(
        self, page_id: str | None = None, space_key: str | None = None, title: str | None = None
    ) -> str:
        """Get Confluence page asynchronously."""
        args: dict[str, Any] = {}
        if page_id:
            args["page_id"] = page_id
        if space_key:
            args["space_key"] = space_key
        if title:
            args["title"] = title

        return await self.clients["confluence"].call_tool_async("confluence_get_page", args)

    async def confluence_search_pages_async(self, cql: str, limit: int = 25) -> str:
        """Search Confluence pages asynchronously."""
        return await self.clients["confluence"].call_tool_async(
            "confluence_search_pages", {"cql": cql, "limit": limit}
        )

    # GitHub methods (official MCP server)

    def github_available(self) -> bool:
        """Check if GitHub MCP client is available."""
        return "github" in self.clients and self.clients["github"].process is not None

    def github_get_file_contents(self, owner: str, repo: str, path: str, branch: str | None = None) -> str:
        """
        Get file contents from a GitHub repository.

        Args:
            owner: Repository owner
            repo: Repository name
            path: File path in repository
            branch: Branch name (optional, defaults to repo default)

        Returns:
            File contents as string
        """
        if not self.github_available():
            raise RuntimeError("GitHub MCP client not available")

        args = {"owner": owner, "repo": repo, "path": path}
        if branch:
            args["branch"] = branch

        return self.clients["github"].call_tool("get_file_contents", args)

    def github_search_code(self, query: str) -> str:
        """
        Search code across GitHub repositories.

        Args:
            query: Search query (GitHub code search syntax)

        Returns:
            Search results as string
        """
        if not self.github_available():
            raise RuntimeError("GitHub MCP client not available")

        return self.clients["github"].call_tool("search_code", {"query": query})

    def github_list_commits(self, owner: str, repo: str, per_page: int = 10, sha: str | None = None) -> str:
        """
        List recent commits in a repository.

        Args:
            owner: Repository owner
            repo: Repository name
            per_page: Number of commits to return (default 10)
            sha: Branch or commit SHA (optional)

        Returns:
            Commits list as string
        """
        if not self.github_available():
            raise RuntimeError("GitHub MCP client not available")

        args = {"owner": owner, "repo": repo, "perPage": per_page}
        if sha:
            args["sha"] = sha

        return self.clients["github"].call_tool("list_commits", args)

    def github_get_pull_request(self, owner: str, repo: str, pull_number: int) -> str:
        """
        Get details of a pull request.

        Args:
            owner: Repository owner
            repo: Repository name
            pull_number: PR number

        Returns:
            PR details as string
        """
        if not self.github_available():
            raise RuntimeError("GitHub MCP client not available")

        return self.clients["github"].call_tool(
            "get_pull_request",
            {"owner": owner, "repo": repo, "pull_number": pull_number}
        )

    def github_list_pull_requests(self, owner: str, repo: str, state: str = "open") -> str:
        """
        List pull requests in a repository.

        Args:
            owner: Repository owner
            repo: Repository name
            state: PR state (open, closed, all)

        Returns:
            PR list as string
        """
        if not self.github_available():
            raise RuntimeError("GitHub MCP client not available")

        # Note: Tool might be named list_pull_requests or get_pull_requests
        try:
            return self.clients["github"].call_tool(
                "list_pull_requests",
                {"owner": owner, "repo": repo, "state": state}
            )
        except RuntimeError:
            # Try alternative tool name
            return self.clients["github"].call_tool(
                "search_pull_requests",
                {"owner": owner, "repo": repo, "state": state}
            )

    def github_get_repository(self, owner: str, repo: str) -> str:
        """
        Get repository information by fetching README or root directory.

        Note: Official GitHub MCP doesn't have get_repository tool,
        so we use get_file_contents on root to verify repo exists.

        Args:
            owner: Repository owner
            repo: Repository name

        Returns:
            Repository info as string
        """
        if not self.github_available():
            raise RuntimeError("GitHub MCP client not available")

        # Use get_file_contents to verify repo exists and get structure
        return self.clients["github"].call_tool(
            "get_file_contents",
            {"owner": owner, "repo": repo, "path": ""}
        )

    def github_get_directory_tree(self, owner: str, repo: str, path: str = "", branch: str | None = None) -> str:
        """
        Get directory structure of a repository path.

        Args:
            owner: Repository owner
            repo: Repository name
            path: Directory path (empty for root)
            branch: Branch name (optional)

        Returns:
            Directory tree as string
        """
        if not self.github_available():
            raise RuntimeError("GitHub MCP client not available")

        args = {"owner": owner, "repo": repo, "path": path}
        if branch:
            args["branch"] = branch

        return self.clients["github"].call_tool("get_file_contents", args)

    # Async GitHub methods

    async def github_get_file_contents_async(self, owner: str, repo: str, path: str, branch: str | None = None) -> str:
        """Get file contents asynchronously."""
        if not self.github_available():
            raise RuntimeError("GitHub MCP client not available")

        args = {"owner": owner, "repo": repo, "path": path}
        if branch:
            args["branch"] = branch

        return await self.clients["github"].call_tool_async("get_file_contents", args)

    async def github_search_code_async(self, query: str) -> str:
        """Search code asynchronously."""
        if not self.github_available():
            raise RuntimeError("GitHub MCP client not available")

        return await self.clients["github"].call_tool_async("search_code", {"query": query})

    def __enter__(self):
        """Context manager entry."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit."""
        self.stop_all()
