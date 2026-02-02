"""
Integration test for MCP servers.

This script validates:
1. Confluence connection and data retrieval
2. Jira connection and data retrieval
3. SDLC rules page existence
4. Data cleaning functionality
"""

import os
import sys
from pathlib import Path
from dotenv import load_dotenv
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from executor.mcp.client import MCPClientManager
from executor.utils.config_loader import load_config

console = Console()


def load_environment() -> dict[str, str]:
    """Load environment variables from .env file."""
    env_file = Path(__file__).parent.parent / ".env"

    if not env_file.exists():
        console.print("[red]Error: .env file not found[/red]")
        console.print(f"Please create {env_file} based on .env.example")
        sys.exit(1)

    load_dotenv(env_file)

    required_vars = [
        "JIRA_URL",
        "JIRA_EMAIL",
        "JIRA_API_TOKEN",
        "CONFLUENCE_URL",
        "CONFLUENCE_EMAIL",
        "CONFLUENCE_API_TOKEN",
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

    return env_vars


def test_confluence_connection(mcp: MCPClientManager, config: dict) -> bool:
    """Test Confluence connection and retrieve space home."""
    console.print("\n[bold blue]Testing Confluence Connection...[/bold blue]")

    try:
        # Get space home from config
        space_home_title = config["confluence"]["space_home_title"]

        # Try to get the space homepage
        # First, we need to know the space key - let's search for it
        cql = f'title = "{space_home_title}"'

        console.print(f"Searching for space: {space_home_title}")
        result = mcp.confluence_search_pages(cql, limit=1)

        console.print(Panel(result, title="Space Home Search Result", border_style="green"))

        console.print("[green]✓ Confluence connection successful[/green]")
        return True

    except Exception as e:
        console.print(f"[red]✗ Confluence connection failed: {e}[/red]")
        return False


def test_sdlc_rules_page(mcp: MCPClientManager, config: dict) -> bool:
    """Test retrieval of SDLC & Workflows Rules page."""
    console.print("\n[bold blue]Testing SDLC Rules Page Retrieval...[/bold blue]")

    try:
        sdlc_rules_title = config["confluence"]["sdlc_rules_page_title"]

        console.print(f"Searching for: {sdlc_rules_title}")
        cql = f'title = "{sdlc_rules_title}"'

        result = mcp.confluence_search_pages(cql, limit=1)

        if "Found 0 pages" in result or not result.strip():
            console.print("[red]✗ SDLC Rules page not found[/red]")
            console.print(f"Expected title: {sdlc_rules_title}")
            return False

        console.print(Panel(result[:500] + "...", title="SDLC Rules (Preview)", border_style="green"))

        console.print("[green]✓ SDLC Rules page found[/green]")
        return True

    except Exception as e:
        console.print(f"[red]✗ Failed to retrieve SDLC Rules: {e}[/red]")
        return False


def test_jira_connection(mcp: MCPClientManager) -> bool:
    """Test Jira connection and search for issues."""
    console.print("\n[bold blue]Testing Jira Connection...[/bold blue]")

    try:
        # Search for issues in AI-TO-DO status
        jql = 'status = "AI-TO-DO" OR status = "Backlog" ORDER BY created DESC'

        console.print(f"Searching with JQL: {jql}")
        result = mcp.jira_search_issues(jql, max_results=5)

        console.print(Panel(result, title="Jira Search Result", border_style="green"))

        console.print("[green]✓ Jira connection successful[/green]")
        return True

    except Exception as e:
        console.print(f"[red]✗ Jira connection failed: {e}[/red]")
        return False


def test_specific_issue(mcp: MCPClientManager, issue_key: str) -> bool:
    """Test retrieval of a specific Jira issue."""
    console.print(f"\n[bold blue]Testing Retrieval of Issue: {issue_key}[/bold blue]")

    try:
        result = mcp.jira_get_issue(issue_key)

        console.print(Panel(result[:1000] + "...", title=f"Issue {issue_key}", border_style="green"))

        console.print(f"[green]✓ Issue {issue_key} retrieved successfully[/green]")
        return True

    except Exception as e:
        console.print(f"[red]✗ Failed to retrieve issue {issue_key}: {e}[/red]")
        return False


def test_data_cleaning(mcp: MCPClientManager) -> bool:
    """Test that HTML cleaning is working."""
    console.print("\n[bold blue]Testing Data Cleaning...[/bold blue]")

    try:
        # Get any Confluence page and verify it's in Markdown
        cql = "type = page ORDER BY created DESC"

        result = mcp.confluence_search_pages(cql, limit=1)

        # Check that result doesn't contain HTML tags
        has_html = "<html>" in result.lower() or "<div>" in result.lower() or "<p>" in result.lower()

        if has_html:
            console.print("[yellow]⚠ Warning: HTML tags found in output (cleaning may not be working)[/yellow]")
            return False

        console.print("[green]✓ Data cleaning appears to be working (no HTML tags detected)[/green]")
        return True

    except Exception as e:
        console.print(f"[red]✗ Data cleaning test failed: {e}[/red]")
        return False


def validate_sdlc_compliance(mcp: MCPClientManager, config: dict) -> bool:
    """Validate that SDLC rules are being followed."""
    console.print("\n[bold blue]Validating SDLC Compliance...[/bold blue]")

    checks = []

    # Check 1: SDLC Rules page exists
    try:
        sdlc_rules_title = config["confluence"]["sdlc_rules_page_title"]
        cql = f'title = "{sdlc_rules_title}"'
        result = mcp.confluence_search_pages(cql, limit=1)

        sdlc_exists = "Found 1 pages" in result or sdlc_rules_title in result
        checks.append(("SDLC Rules Page Exists", sdlc_exists))
    except Exception:
        checks.append(("SDLC Rules Page Exists", False))

    # Check 2: Product Registry exists
    try:
        product_registry_title = config["confluence"]["product_registry_title"]
        cql = f'title = "{product_registry_title}"'
        result = mcp.confluence_search_pages(cql, limit=1)

        registry_exists = "Found 1 pages" in result or product_registry_title in result
        checks.append(("Product Registry Exists", registry_exists))
    except Exception:
        checks.append(("Product Registry Exists", False))

    # Check 3: Jira statuses match config
    try:
        expected_statuses = [
            "Backlog",
            "AI-TO-DO",
            "Analysis",
            "Human Plan Review",
            "Ready for Dev",
            "In Progress",
            "Review",
            "Deployment",
            "Done",
        ]

        # Search for issues with these statuses
        status_checks = []
        for status in expected_statuses[:3]:  # Test first 3 statuses
            jql = f'status = "{status}"'
            try:
                result = mcp.jira_search_issues(jql, max_results=1)
                # If query succeeds, status exists
                status_checks.append(True)
            except Exception:
                status_checks.append(False)

        statuses_valid = any(status_checks)  # At least one status should exist
        checks.append(("Jira Statuses Valid", statuses_valid))
    except Exception:
        checks.append(("Jira Statuses Valid", False))

    # Display results
    table = Table(title="SDLC Compliance Checks")
    table.add_column("Check", style="cyan")
    table.add_column("Status", style="bold")

    for check_name, passed in checks:
        status = "[green]✓ PASS[/green]" if passed else "[red]✗ FAIL[/red]"
        table.add_row(check_name, status)

    console.print(table)

    all_passed = all(passed for _, passed in checks)

    if all_passed:
        console.print("[green]✓ All SDLC compliance checks passed[/green]")
    else:
        console.print("[yellow]⚠ Some SDLC compliance checks failed[/yellow]")

    return all_passed


def main():
    """Run all integration tests."""
    console.print(Panel.fit(
        "[bold cyan]MCP Integration Test Suite[/bold cyan]\n"
        "Testing Confluence & Jira MCP Servers",
        border_style="cyan",
    ))

    # Load environment
    console.print("\n[bold]Step 1: Loading Environment...[/bold]")
    env_vars = load_environment()
    console.print("[green]✓ Environment loaded[/green]")

    # Load config
    console.print("\n[bold]Step 2: Loading Configuration...[/bold]")
    config_path = Path(__file__).parent.parent / "config" / "sdlc_config.yaml"
    config = load_config(config_path)
    console.print(f"[green]✓ Configuration loaded from {config_path}[/green]")

    # Initialize MCP manager
    console.print("\n[bold]Step 3: Starting MCP Servers...[/bold]")
    mcp = MCPClientManager()

    try:
        mcp.start_all(env_vars)
        console.print("[green]✓ MCP servers started[/green]")

        # Run tests
        results = []

        results.append(("Confluence Connection", test_confluence_connection(mcp, config.model_dump())))
        results.append(("SDLC Rules Page", test_sdlc_rules_page(mcp, config.model_dump())))
        results.append(("Jira Connection", test_jira_connection(mcp)))
        results.append(("Data Cleaning", test_data_cleaning(mcp)))
        results.append(("SDLC Compliance", validate_sdlc_compliance(mcp, config.model_dump())))

        # Test specific feature: WEB3-3
        results.append(("WEB3-3 Feature", test_specific_issue(mcp, "WEB3-3")))

        # Optional: Test additional issue if provided
        if len(sys.argv) > 1:
            issue_key = sys.argv[1]
            results.append((f"Issue {issue_key}", test_specific_issue(mcp, issue_key)))

        # Summary
        console.print("\n" + "=" * 60)
        console.print("[bold cyan]Test Summary[/bold cyan]")
        console.print("=" * 60)

        summary_table = Table()
        summary_table.add_column("Test", style="cyan")
        summary_table.add_column("Result", style="bold")

        for test_name, passed in results:
            status = "[green]✓ PASS[/green]" if passed else "[red]✗ FAIL[/red]"
            summary_table.add_row(test_name, status)

        console.print(summary_table)

        all_passed = all(passed for _, passed in results)

        if all_passed:
            console.print("\n[bold green]✓ All tests passed![/bold green]")
            return 0
        else:
            console.print("\n[bold yellow]⚠ Some tests failed[/bold yellow]")
            return 1

    except Exception as e:
        console.print(f"\n[bold red]✗ Error during testing: {e}[/bold red]")
        import traceback

        traceback.print_exc()
        return 1

    finally:
        console.print("\n[bold]Step 4: Stopping MCP Servers...[/bold]")
        mcp.stop_all()
        console.print("[green]✓ MCP servers stopped[/green]")


if __name__ == "__main__":
    sys.exit(main())
