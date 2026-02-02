#!/usr/bin/env python3
"""
AI-SWARM MVP Executor
Simple CLI to execute features from Jira

Usage:
    python3 execute.py --task WEB3-3
    python3 execute.py --task https://your-domain.atlassian.net/browse/WEB3-3
"""

import argparse
import os
import sys
import re
from pathlib import Path
from dotenv import load_dotenv
from rich.console import Console
from rich.panel import Panel
from rich.markdown import Markdown

# Add src to path
sys.path.insert(0, str(Path(__file__).parent / "src"))

from executor.mcp.client import MCPClientManager
from executor.utils.config_loader import load_config

console = Console()


def parse_jira_key(task_input: str) -> str:
    """
    Parse Jira issue key from various input formats.

    Examples:
        - WEB3-3 → WEB3-3
        - https://your-domain.atlassian.net/browse/WEB3-3 → WEB3-3
        - your-domain.atlassian.net/browse/WEB3-3 → WEB3-3
    """
    # If it's already a simple key format (PROJECT-123 or WEB3-3)
    if re.match(r'^[A-Z0-9]+-\d+$', task_input):
        return task_input

    # Extract from URL
    match = re.search(r'([A-Z0-9]+-\d+)', task_input)
    if match:
        return match.group(1)

    raise ValueError(f"Could not parse Jira issue key from: {task_input}")


def load_environment() -> dict[str, str]:
    """Load environment variables from .env file."""
    env_file = Path(__file__).parent / ".env"

    if not env_file.exists():
        console.print("[red]Error: .env file not found[/red]")
        console.print(f"Please create {env_file} based on .env.example")
        sys.exit(1)

    load_dotenv(env_file)

    required_vars = [
        "JIRA_URL",
        "JIRA_EMAIL",
        "JIRA_API_TOKEN",
    ]

    env_vars = {}
    missing_vars = []

    for var in required_vars:
        value = os.getenv(var)
        if not value:
            missing_vars.append(var)
        else:
            env_vars[var] = value

    if missing_vars:
        console.print("[red]Error: Missing required environment variables:[/red]")
        for var in missing_vars:
            console.print(f"  - {var}")
        sys.exit(1)

    # Optional Confluence variables
    env_vars["CONFLUENCE_URL"] = os.getenv("CONFLUENCE_URL", "")
    env_vars["CONFLUENCE_EMAIL"] = os.getenv("CONFLUENCE_EMAIL", "")
    env_vars["CONFLUENCE_API_TOKEN"] = os.getenv("CONFLUENCE_API_TOKEN", "")

    return env_vars


def execute_feature(issue_key: str):
    """
    Execute a feature from Jira (MVP version).

    Currently just fetches and displays the feature.
    Future: Full SDLC workflow automation.
    """
    console.print(Panel.fit(
        f"[bold cyan]AI-SWARM Executor (MVP)[/bold cyan]\n"
        f"Processing Feature: {issue_key}",
        border_style="cyan",
    ))

    # Load environment
    console.print("\n[bold]Step 1: Loading Environment...[/bold]")
    env_vars = load_environment()
    console.print("[green]✓ Environment loaded[/green]")

    # Load config
    console.print("\n[bold]Step 2: Loading Configuration...[/bold]")
    config_path = Path(__file__).parent / "config" / "sdlc_config.yaml"
    config = load_config(config_path)
    console.print(f"[green]✓ Configuration loaded[/green]")

    # Initialize MCP manager
    console.print("\n[bold]Step 3: Starting MCP Servers...[/bold]")
    mcp = MCPClientManager()

    try:
        mcp.start_all(env_vars)
        console.print("[green]✓ MCP servers started[/green]")

        # Fetch the feature
        console.print(f"\n[bold]Step 4: Fetching Feature {issue_key}...[/bold]")

        try:
            issue_data = mcp.jira_get_issue(issue_key)
            console.print("[green]✓ Feature retrieved successfully[/green]")

            # Display the feature
            console.print("\n" + "=" * 70)
            console.print("[bold cyan]Feature Details[/bold cyan]")
            console.print("=" * 70)

            # Render as Markdown
            md = Markdown(issue_data)
            console.print(md)

            console.print("\n" + "=" * 70)

            # MVP: Just display, no automation yet
            console.print("\n[yellow]MVP Mode: Feature retrieved but not processed[/yellow]")
            console.print("[dim]Full SDLC automation coming in next version...[/dim]")

            # Future: Phase detection and routing
            console.print("\n[bold]Next Steps (Manual for MVP):[/bold]")
            console.print("  1. Review feature requirements")
            console.print("  2. Check Definition of Ready")
            console.print("  3. Load Project Passport from Confluence")
            console.print("  4. Create implementation plan")

            return 0

        except Exception as e:
            console.print(f"[red]✗ Failed to retrieve feature: {e}[/red]")
            return 1

    except Exception as e:
        console.print(f"\n[bold red]✗ Error during execution: {e}[/bold red]")
        import traceback
        traceback.print_exc()
        return 1

    finally:
        console.print("\n[bold]Step 5: Stopping MCP Servers...[/bold]")
        mcp.stop_all()
        console.print("[green]✓ MCP servers stopped[/green]")


def main():
    """Main CLI entry point."""
    parser = argparse.ArgumentParser(
        description="AI-SWARM Executor - Automated SDLC Feature Execution",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python3 execute.py --task WEB3-3
  python3 execute.py --task https://your-domain.atlassian.net/browse/WEB3-3
  python3 execute.py -t WEB3-3
        """
    )

    parser.add_argument(
        "--task", "-t",
        required=True,
        help="Jira issue key or URL (e.g., WEB3-3 or https://your-domain.atlassian.net/browse/WEB3-3)"
    )

    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Dry run mode (fetch only, no modifications)"
    )

    args = parser.parse_args()

    try:
        # Parse the Jira issue key
        issue_key = parse_jira_key(args.task)
        console.print(f"[dim]Parsed issue key: {issue_key}[/dim]\n")

        # Execute the feature
        return execute_feature(issue_key)

    except ValueError as e:
        console.print(f"[red]Error: {e}[/red]")
        return 1
    except KeyboardInterrupt:
        console.print("\n[yellow]Execution cancelled by user[/yellow]")
        return 130


if __name__ == "__main__":
    sys.exit(main())
