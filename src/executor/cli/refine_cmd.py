"""Refinement pipeline command."""
import os
import logging
from pathlib import Path
from rich.panel import Panel
from rich.markdown import Markdown

from .shared import console, load_environment
from ..mcp.client import MCPClientManager
from ..utils.config_loader import load_config
from ..phases import (
    parse_issue_key,
    LLMExecutor,
    handle_post_execution,
)
from ..phases.context_store import load_context

logger = logging.getLogger(__name__)


def refinement_pipeline(
    task_input: str,
    feedback: str,
    output_dir: str = "outputs",
    json_logs: bool = False,
) -> int:
    """
    Refine a previous plan using human feedback.

    Loads saved context from a previous execution and re-runs Stage 5
    with a refinement prompt that incorporates the feedback.

    Args:
        task_input: Jira issue key or URL
        feedback: Human feedback text
        output_dir: Output directory
        json_logs: If True, use JSON structured logging

    Returns:
        Exit code (0 = success, 1 = error)
    """
    issue_key = parse_issue_key(task_input)

    console.print(Panel.fit(
        f"[bold cyan]AI-SWARM Refinement[/bold cyan]\n"
        f"Refining: {issue_key}",
        border_style="cyan",
    ))

    # Load saved context
    console.print("\n[bold]Step 1: Loading saved context[/bold]")
    execution_context = load_context(issue_key, output_dir)

    if not execution_context:
        console.print("  [red]✗ No saved context found[/red]")
        console.print(f"  Run the full pipeline first: python3 execute.py --task {issue_key}")
        return 1

    console.print(f"  [green]✓[/green] Loaded context from {execution_context.timestamp.isoformat()}")

    # Find the latest plan version
    console.print("\n[bold]Step 2: Loading previous plan[/bold]")
    issue_dir = Path(output_dir) / issue_key

    # Find the latest plan file (plan.md, plan_v2.md, plan_v3.md, ...)
    plan_files = sorted(issue_dir.glob(f"{issue_key}_plan*.md"))
    if not plan_files:
        console.print("  [red]✗ No previous plan found[/red]")
        return 1

    latest_plan_file = plan_files[-1]
    previous_plan = latest_plan_file.read_text(encoding="utf-8")

    # Determine version number
    version = 2
    if "_v" in latest_plan_file.stem:
        try:
            version = int(latest_plan_file.stem.split("_v")[-1]) + 1
        except ValueError:
            version = 2

    console.print(f"  [green]✓[/green] Loaded: {latest_plan_file.name}")
    console.print(f"  Generating refined plan v{version}")

    # Check DeepSeek API key
    deepseek_key = os.getenv("DEEPSEEK_API_KEY")
    if not deepseek_key:
        # Need to load env
        load_environment()
        deepseek_key = os.getenv("DEEPSEEK_API_KEY")

    if not deepseek_key:
        console.print("  [red]✗ DEEPSEEK_API_KEY not found[/red]")
        return 1

    # Load config for model selection
    config_path = Path(__file__).parent.parent.parent.parent / "config" / "sdlc_config.yaml"
    config = load_config(config_path)
    agent_config = config.model_dump().get("agent", {})
    model = agent_config.get("model", "deepseek-chat")

    # Execute refinement (no MCP needed — just LLM call)
    console.print(f"\n[bold]Step 3: Calling LLM for refinement[/bold]")
    console.print(f"  Feedback: {feedback[:100]}{'...' if len(feedback) > 100 else ''}")

    executor = LLMExecutor(
        api_key=deepseek_key,
        model=model,
        output_dir=output_dir,
    )

    try:
        response, output = executor.execute_refinement(
            context=execution_context,
            feedback=feedback,
            previous_plan=previous_plan,
            version=version,
        )
    except Exception as e:
        console.print(f"  [red]✗ LLM call failed: {e}[/red]")
        return 1

    console.print(f"  [green]✓[/green] Refined plan generated ({response.tokens_used} tokens)")
    console.print(f"  [green]✓[/green] Plan: {output.plan_file}")
    console.print(f"  [green]✓[/green] Reasoning: {output.reasoning_file}")

    # Show refined work plan summary
    console.print("\n[bold]Refined Work Plan v{version}:[/bold]")
    console.print("=" * 70)
    if response.work_plan:
        console.print(Markdown(response.work_plan[:1500]))
    else:
        console.print("[yellow]Work plan section not found[/yellow]")
    console.print("=" * 70)

    # Optionally post to Jira (requires MCP)
    console.print(f"\n[bold]Step 4: Post to Jira[/bold]")

    env_vars = load_environment()
    mcp = MCPClientManager()

    try:
        mcp.start_all(env_vars)
        console.print("  [green]✓[/green] MCP servers started")

        result = handle_post_execution(
            mcp=mcp,
            issue_key=issue_key,
            execution_context=execution_context,
            plan_summary=response.work_plan[:1000] if response.work_plan else None,
            llm_response=response,
            config=config.model_dump(),
            dry_run=False,
        )

        if result.decomposition_result:
            dr = result.decomposition_result
            console.print(f"  [green]✓[/green] Parsed {len(dr.stories)} stories (Refined Plan v{version})")
            if dr.review_task_key:
                console.print(f"  [green]✓[/green] Updated PLAN REVIEW task: {dr.review_task_key}")

        if result.error:
            console.print(f"  [yellow]⚠[/yellow] Transition: {result.error}")
        else:
            console.print(f"  [green]✓[/green] Transitioned to {result.target_status}")

    except Exception as e:
        console.print(f"  [yellow]⚠[/yellow] Jira update failed: {e}")
        console.print("  Refined plan is saved locally but not posted to Jira.")

    finally:
        mcp.stop_all()

    console.print(f"\n[bold green]✓ Refinement v{version} completed[/bold green]")
    return 0
