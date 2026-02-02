"""
MCP Client Manager for interacting with custom MCP servers.

This client wraps the MCP protocol and provides a simple interface
for the Executor Agent to interact with Jira and Confluence.
"""

import subprocess
import json
import logging
from typing import Any
from pathlib import Path

logger = logging.getLogger(__name__)


class MCPClient:
    """
    Client for interacting with a single MCP server.

    This client communicates with MCP servers via stdio.
    """

    def __init__(self, server_script: str, env: dict[str, str] | None = None):
        """
        Initialize MCP client.

        Args:
            server_script: Path to MCP server script
            env: Environment variables for server
        """
        self.server_script = server_script
        self.env = env or {}
        self.process: subprocess.Popen | None = None

    def start(self) -> None:
        """Start the MCP server process."""
        if self.process:
            logger.warning("Server already running")
            return

        import os
        import sys

        env = os.environ.copy()
        env.update(self.env)

        self.process = subprocess.Popen(
            [sys.executable, self.server_script],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            text=True,
        )

        logger.info(f"Started MCP server: {self.server_script}")

        # Perform MCP initialization handshake
        self._initialize()

    def _initialize(self) -> None:
        """Perform MCP protocol initialization handshake."""
        if not self.process:
            raise RuntimeError("Server not started")

        # Send initialize request
        init_request = {
            "jsonrpc": "2.0",
            "id": 0,
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

        request_json = json.dumps(init_request) + "\n"
        self.process.stdin.write(request_json)  # type: ignore
        self.process.stdin.flush()  # type: ignore

        # Read initialize response
        response_line = self.process.stdout.readline()  # type: ignore
        response = json.loads(response_line)

        if "error" in response:
            raise RuntimeError(f"MCP initialization failed: {response['error']}")

        # Send initialized notification
        initialized_notification = {
            "jsonrpc": "2.0",
            "method": "notifications/initialized"
        }

        notification_json = json.dumps(initialized_notification) + "\n"
        self.process.stdin.write(notification_json)  # type: ignore
        self.process.stdin.flush()  # type: ignore

        logger.info("MCP initialization handshake completed")

    def stop(self) -> None:
        """Stop the MCP server process."""
        if self.process:
            self.process.terminate()
            self.process.wait()
            self.process = None
            logger.info(f"Stopped MCP server: {self.server_script}")

    def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> str:
        """
        Call a tool on the MCP server.

        Args:
            tool_name: Name of the tool
            arguments: Tool arguments

        Returns:
            Tool result as string
        """
        if not self.process:
            raise RuntimeError("Server not started")

        # Build MCP request
        request = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": tool_name,
                "arguments": arguments,
            },
        }

        # Send request
        request_json = json.dumps(request) + "\n"
        self.process.stdin.write(request_json)  # type: ignore
        self.process.stdin.flush()  # type: ignore

        # Read response
        response_line = self.process.stdout.readline()  # type: ignore
        response = json.loads(response_line)

        # Extract result
        if "error" in response:
            raise RuntimeError(f"MCP error: {response['error']}")

        result = response.get("result", {})

        # Extract text content
        content = result.get("content", [])
        if content and len(content) > 0:
            return content[0].get("text", "")

        return ""

    def __enter__(self):
        """Context manager entry."""
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit."""
        self.stop()


class MCPClientManager:
    """
    Manager for multiple MCP clients (Jira, Confluence, etc.).

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
        # Start Jira server
        jira_server = self.server_dir / "jira_server.py"
        self.clients["jira"] = MCPClient(
            str(jira_server),
            env={
                "JIRA_URL": env_vars.get("JIRA_URL", ""),
                "JIRA_EMAIL": env_vars.get("JIRA_EMAIL", ""),
                "JIRA_API_TOKEN": env_vars.get("JIRA_API_TOKEN", ""),
            },
        )
        self.clients["jira"].start()

        # Start Confluence server
        confluence_server = self.server_dir / "confluence_server.py"
        self.clients["confluence"] = MCPClient(
            str(confluence_server),
            env={
                "CONFLUENCE_URL": env_vars.get("CONFLUENCE_URL", ""),
                "CONFLUENCE_EMAIL": env_vars.get("CONFLUENCE_EMAIL", ""),
                "CONFLUENCE_API_TOKEN": env_vars.get("CONFLUENCE_API_TOKEN", ""),
            },
        )
        self.clients["confluence"].start()

        logger.info("All MCP servers started")

    def stop_all(self) -> None:
        """Stop all MCP servers."""
        for client in self.clients.values():
            client.stop()

        self.clients.clear()
        logger.info("All MCP servers stopped")

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
        return self.clients["jira"].call_tool("jira_add_comment", {"issue_key": issue_key, "body": body})

    def jira_transition_issue(self, issue_key: str, transition_name: str) -> str:
        """Transition Jira issue."""
        return self.clients["jira"].call_tool(
            "jira_transition_issue", {"issue_key": issue_key, "transition_name": transition_name}
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
        return self.clients["confluence"].call_tool("confluence_get_space_home", {"space_key": space_key})

    def __enter__(self):
        """Context manager entry."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit."""
        self.stop_all()
