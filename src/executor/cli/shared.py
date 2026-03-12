"""Shared CLI state: console, environment loader."""
import os
import sys
import logging
from pathlib import Path
from dotenv import load_dotenv
from rich.console import Console

logger = logging.getLogger(__name__)

# Module-level console (can be replaced in main() for json mode)
console = Console()


def load_environment() -> dict[str, str]:
    """Load environment variables from .env file."""
    env_file = Path(__file__).parent.parent.parent.parent / ".env"

    if not env_file.exists():
        console.print("[red]Error: .env file not found[/red]")
        console.print("Please create .env based on .env.example:")
        console.print("  cp .env.example .env")
        sys.exit(1)

    load_dotenv(env_file)

    env_vars = {}

    # Atlassian URL (shared for Jira and Confluence)
    atlassian_url = os.getenv("ATLASSIAN_URL")
    if not atlassian_url:
        console.print("[red]Error: ATLASSIAN_URL is required[/red]")
        sys.exit(1)
    env_vars["ATLASSIAN_URL"] = atlassian_url

    # Confluence URL (with /wiki suffix for Confluence Cloud)
    confluence_url = os.getenv("CONFLUENCE_URL", "")
    if not confluence_url:
        confluence_url = atlassian_url.rstrip("/") + "/wiki"
    env_vars["CONFLUENCE_URL"] = confluence_url

    # Bot account credentials (used for all Jira/Confluence operations)
    bot_email = os.getenv("ATLASSIAN_BOT_EMAIL", "")
    bot_token = os.getenv("ATLASSIAN_BOT_API_TOKEN", "")

    # Admin credentials (fallback, not recommended for automation)
    admin_email = os.getenv("ATLASSIAN_ADMIN_EMAIL", "")
    admin_token = os.getenv("ATLASSIAN_ADMIN_API_TOKEN", "")

    # Prefer bot account, fallback to admin
    env_vars["ATLASSIAN_EMAIL"] = bot_email or admin_email
    env_vars["ATLASSIAN_API_TOKEN"] = bot_token or admin_token

    if not env_vars["ATLASSIAN_EMAIL"] or not env_vars["ATLASSIAN_API_TOKEN"]:
        console.print("[red]Error: Missing Atlassian credentials[/red]")
        console.print("Set ATLASSIAN_BOT_EMAIL/ATLASSIAN_BOT_API_TOKEN (recommended)")
        console.print("Or ATLASSIAN_ADMIN_EMAIL/ATLASSIAN_ADMIN_API_TOKEN (fallback)")
        sys.exit(1)

    # Log which account is being used
    account_type = "bot" if bot_email else "admin"
    console.print(f"  Using Atlassian {account_type} account: {env_vars['ATLASSIAN_EMAIL']}")

    # Optional GitHub token for codebase context
    env_vars["GITHUB_TOKEN"] = os.getenv("GITHUB_TOKEN", "")

    return env_vars
