"""Phase 0: Backlog Analysis pipeline command."""
import os
import logging
from pathlib import Path
from rich.panel import Panel
from rich.markdown import Markdown
from rich.progress import Progress, SpinnerColumn, TextColumn
from openai import OpenAI

from .shared import console, load_environment
from ..mcp.client import MCPClientManager
from ..utils.config_loader import load_config
from ..phases import (
    parse_issue_key,
    execute_phase_zero,
)

logger = logging.getLogger(__name__)


def phase_zero_pipeline(
    task_input: str,
    dry_run: bool = False,
    output_dir: str = "outputs",
) -> int:
    """
    Execute Phase 0: Backlog Analysis pipeline.

    Transforms raw Jira backlog descriptions into structured Use Cases
    and Definition of Ready. Output goes to Jira only (Confluence read-only).

    Args:
        task_input: Jira issue key or URL
        dry_run: If True, skip Jira comment posting
        output_dir: Directory for output files

    Returns:
        Exit code (0 = success, 1 = error)
    """
    issue_key = parse_issue_key(task_input)

    console.print(Panel.fit(
        f"[bold cyan]AI-SWARM Phase 0: Backlog Analysis[/bold cyan]\n"
        f"Processing: {issue_key}" + (" [dry-run]" if dry_run else ""),
        border_style="cyan",
    ))

    # Load environment
    console.print("\n[bold]Initialization[/bold]")
    env_vars = load_environment()
    console.print("  [green]✓[/green] Environment loaded")

    # Load config
    config_path = Path(__file__).parent.parent.parent.parent / "config" / "sdlc_config.yaml"
    config = load_config(config_path)
    console.print("  [green]✓[/green] Configuration loaded")

    # Check DeepSeek API key
    deepseek_key = os.getenv("DEEPSEEK_API_KEY")
    if not deepseek_key:
        console.print("  [red]✗ DEEPSEEK_API_KEY not found[/red]")
        return 1

    deepseek_client = OpenAI(
        api_key=deepseek_key,
        base_url="https://api.deepseek.com"
    )

    agent_config = config.model_dump().get("agent", {})
    model = agent_config.get("model", "deepseek-chat")
    console.print(f"  [green]✓[/green] DeepSeek client initialized (model: {model})")

    # Initialize MCP manager
    mcp = MCPClientManager()

    try:
        console.print("  Starting MCP servers...")
        mcp.start_all(env_vars)
        console.print("  [green]✓[/green] MCP servers started")

        # Execute Phase 0
        console.print("\n[bold]Phase 0: Backlog Analysis[/bold]")

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
            transient=True,
        ) as progress:
            task = progress.add_task("Building context & analyzing requirements...", total=None)

            result = execute_phase_zero(
                mcp=mcp,
                llm_client=deepseek_client,
                issue_key=issue_key,
                config=config.model_dump(),
                output_dir=output_dir,
                model=model,
                dry_run=dry_run,
            )

        # Handle errors
        if result.error:
            console.print(f"\n[red]✗ Phase 0 failed: {result.error}[/red]")
            return 1

        # Display results
        resp = result.response
        console.print(f"  [green]✓[/green] LLM response received ({resp.tokens_used} tokens)")
        console.print(f"  [green]✓[/green] Feature Type: {resp.feature_type}")
        console.print(f"  [green]✓[/green] Complexity: {resp.complexity_estimate}")

        if result.validation_errors:
            console.print(f"\n  [yellow]⚠ Validation warnings:[/yellow]")
            for err in result.validation_errors:
                console.print(f"    - {err}")
        else:
            console.print(f"  [green]✓[/green] Validation passed")

        if result.output_file:
            console.print(f"  [green]✓[/green] Output: {result.output_file}")

        if result.jira_updated:
            console.print(f"  [green]✓[/green] Jira description updated for {issue_key}")
        elif dry_run:
            console.print(f"  [dim]  Jira update skipped (dry-run)[/dim]")

        # DoR status
        if result.dor_met:
            console.print(f"\n  [bold green]✓ Definition of Ready: MET[/bold green]")
            console.print(f"  Issue {issue_key} is ready for AI-TO-DO transition.")
        else:
            console.print(f"\n  [bold yellow]⚠ Definition of Ready: NOT MET[/bold yellow]")
            if result.validation_errors:
                console.print(f"  Analysis has validation issues.")

        # Show summary
        console.print("\n[bold]Analysis Summary:[/bold]")
        console.print("=" * 70)
        if resp.chain_of_thought:
            console.print(Markdown(resp.chain_of_thought[:1500]))
        console.print("=" * 70)

        console.print("\n[bold green]✓ Phase 0 completed successfully[/bold green]")
        return 0

    except Exception as e:
        console.print(f"\n[bold red]✗ Error during Phase 0: {e}[/bold red]")
        import traceback
        traceback.print_exc()
        return 1

    finally:
        console.print("\n[dim]Stopping MCP servers...[/dim]")
        mcp.stop_all()
