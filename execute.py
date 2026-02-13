#!/usr/bin/env python3
"""
AI-SWARM Executor - 5-Stage Pipeline

Stages:
1. Trigger - Parse Jira issue key
2. Jira Enrichment - Extract context from Jira
3. Confluence Knowledge - Retrieve project knowledge
4. Data Aggregation - Build unified context
5. LLM Execution - Generate work plan via DeepSeek

Usage:
    python3 execute.py --task PROJ-123
    python3 execute.py --task PROJ-123 --dry-run  # Skip LLM call
    python3 execute.py --task PROJ-123 --output-dir ./my_outputs
"""

import argparse
import os
import sys
import logging
from pathlib import Path
from dotenv import load_dotenv
from rich.console import Console
from rich.panel import Panel
from rich.markdown import Markdown
from rich.progress import Progress, SpinnerColumn, TextColumn

# Add src to path
sys.path.insert(0, str(Path(__file__).parent / "src"))

from executor.mcp.client import MCPClientManager
from executor.utils.config_loader import load_config
from executor.phases import (
    parse_issue_key,
    build_context_pipeline,
    build_refined_context_pipeline,
    LLMExecutor,
    handle_post_execution,
    ExecutionOutcome,
)
from openai import OpenAI

console = Console()
logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)


def load_environment() -> dict[str, str]:
    """Load environment variables from .env file."""
    env_file = Path(__file__).parent / ".env"

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


def execute_pipeline(
    task_input: str,
    dry_run: bool = False,
    output_dir: str = "outputs",
) -> int:
    """
    Execute the full 5-stage pipeline.

    Args:
        task_input: Jira issue key or URL
        dry_run: If True, skip LLM execution (Stages 1-4 only)
        output_dir: Directory for output files

    Returns:
        Exit code (0 = success, 1 = error)
    """
    # Stage 1: Parse issue key
    issue_key = parse_issue_key(task_input)

    console.print(Panel.fit(
        f"[bold cyan]AI-SWARM Executor[/bold cyan]\n"
        f"Processing: {issue_key}" + (" [dry-run]" if dry_run else ""),
        border_style="cyan",
    ))

    # Load environment
    console.print("\n[bold]Stage 0: Initialization[/bold]")
    env_vars = load_environment()
    console.print("  [green]✓[/green] Environment loaded")

    # Load config
    config_path = Path(__file__).parent / "config" / "sdlc_config.yaml"
    config = load_config(config_path)
    console.print("  [green]✓[/green] Configuration loaded")

    # Check DeepSeek API key for Stage 5 and Two-Stage Retrieval
    deepseek_key = os.getenv("DEEPSEEK_API_KEY")
    deepseek_client = None

    if deepseek_key:
        deepseek_client = OpenAI(
            api_key=deepseek_key,
            base_url="https://api.deepseek.com"
        )
        console.print("  [green]✓[/green] DeepSeek client initialized (Two-Stage Retrieval enabled)")
    else:
        console.print("  [yellow]⚠[/yellow] DEEPSEEK_API_KEY not found - using legacy pipeline")
        if not dry_run:
            console.print("  [yellow]⚠[/yellow] Stage 5 will be skipped")
            dry_run = True

    # Initialize MCP manager
    mcp = MCPClientManager()

    try:
        console.print("  Starting MCP servers...")
        mcp.start_all(env_vars)
        console.print("  [green]✓[/green] MCP servers started")

        # =================================================================
        # Stages 2-4: Context Building
        # =================================================================
        console.print("\n[bold]Stages 2-4: Building Context[/bold]")

        execution_context = None
        context_error = None

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
            transient=True,
        ) as progress:
            task = progress.add_task("Stage 2: Jira Enrichment...", total=None)

            # Build context (Stages 2-4)
            sdlc_title = config.model_dump().get("confluence", {}).get(
                "sdlc_rules_page_title", "SDLC & Workflows Rules"
            )

            try:
                # Use Two-Stage Retrieval if DeepSeek is available
                if deepseek_client:
                    progress.update(task, description="Stage 2: Jira Enrichment...")
                    execution_context = build_refined_context_pipeline(
                        mcp=mcp,
                        llm_client=deepseek_client,
                        task_input=issue_key,
                        config=config.model_dump(),
                    )
                    progress.update(task, description="Stage 3: Confluence Knowledge (LLM Filtering)...")
                    progress.update(task, description="Stage 4: Data Aggregation...")
                else:
                    # Legacy pipeline (no LLM filtering)
                    execution_context = build_context_pipeline(
                        mcp=mcp,
                        task_input=issue_key,
                        sdlc_rules_title=sdlc_title,
                    )
                    progress.update(task, description="Stage 3: Confluence Knowledge...")
                    progress.update(task, description="Stage 4: Data Aggregation...")
            except Exception as e:
                context_error = e
                logger.error(f"Context building failed: {e}")

        # Handle context building failure
        if context_error:
            console.print(f"\n[red]✗ Context building failed: {context_error}[/red]")

            # Post-execution: transition to Backlog
            if not dry_run:
                console.print("\n[bold]Post-Execution: Transitioning to Backlog[/bold]")
                result = handle_post_execution(
                    mcp=mcp,
                    issue_key=issue_key,
                    execution_context=execution_context,
                    execution_error=context_error,
                    dry_run=False,
                )
                if result.error:
                    console.print(f"  [yellow]⚠[/yellow] Transition failed: {result.error}")
                else:
                    console.print(f"  [green]✓[/green] Transitioned to {result.target_status}")
                    console.print(f"  [green]✓[/green] Added explanation comment")
            return 1

        # Display context summary
        console.print(f"  [green]✓[/green] Jira: {execution_context.jira.summary[:50]}...")

        # Display Confluence context based on pipeline type
        if execution_context.refined_confluence:
            # Two-Stage Retrieval results
            rc = execution_context.refined_confluence
            console.print(f"  [green]✓[/green] Space: {rc.project_space}")
            console.print(f"  [green]✓[/green] Project Status: {rc.project_status.value}")

            # Show Core Documents (Mandatory Path)
            if rc.core_documents:
                console.print(f"\n  [bold cyan]📚 Core Documents (Mandatory):[/bold cyan]")
                for doc in rc.core_documents:
                    console.print(f"    • {doc.title}")
            else:
                console.print(f"  [yellow]⚠[/yellow] No core documents found (new project signal)")

            # Show LLM-Selected Documents (Discovery Path)
            if rc.supporting_documents:
                console.print(f"\n  [bold cyan]🔍 LLM-Selected Documents ({len(rc.supporting_documents)}):[/bold cyan]")
                for doc in rc.supporting_documents:
                    console.print(f"    • {doc.title}")
            else:
                console.print(f"  [dim]  No supporting documents selected by LLM[/dim]")

            # Show missing critical data (new project signal)
            if rc.missing_critical_data:
                console.print(f"\n  [yellow]⚠ New Project Signal - Missing:[/yellow]")
                for item in rc.missing_critical_data:
                    console.print(f"    - {item}")

            # Show retrieval errors
            if rc.retrieval_errors:
                for err in rc.retrieval_errors:
                    console.print(f"  [yellow]⚠[/yellow] {err}")

        elif execution_context.confluence:
            # Legacy pipeline
            console.print(f"  [green]✓[/green] Space: {execution_context.confluence.space_key}")
            if execution_context.confluence.retrieval_errors:
                for err in execution_context.confluence.retrieval_errors:
                    console.print(f"  [yellow]⚠[/yellow] {err}")

        if not execution_context.is_valid():
            console.print("\n[red]✗ Context validation failed[/red]")
            for err in execution_context.errors:
                console.print(f"  - {err}")

            # Post-execution: transition to Backlog
            if not dry_run:
                console.print("\n[bold]Post-Execution: Transitioning to Backlog[/bold]")
                result = handle_post_execution(
                    mcp=mcp,
                    issue_key=issue_key,
                    execution_context=execution_context,
                    dry_run=False,
                )
                if result.error:
                    console.print(f"  [yellow]⚠[/yellow] Transition failed: {result.error}")
                else:
                    console.print(f"  [green]✓[/green] Transitioned to {result.target_status}")
                    console.print(f"  [green]✓[/green] Added explanation comment")
            return 1

        # =================================================================
        # Stage 5: LLM Execution (or dry-run)
        # =================================================================
        if dry_run:
            console.print("\n[bold]Stage 5: LLM Execution [SKIPPED - dry-run][/bold]")

            # Save context file only
            output_path = Path(output_dir) / issue_key
            output_path.mkdir(parents=True, exist_ok=True)

            context_file = output_path / f"{issue_key}_context.md"
            context_file.write_text(
                f"# Context for {issue_key}\n\n"
                f"Generated: {execution_context.timestamp.isoformat()}\n\n"
                f"---\n\n"
                f"{execution_context.build_prompt_context()}",
                encoding="utf-8"
            )

            console.print(f"  [green]✓[/green] Context saved: {context_file}")

            # Show preview
            console.print("\n[bold]Context Preview:[/bold]")
            console.print("=" * 70)
            preview = execution_context.build_prompt_context()[:2000]
            console.print(Markdown(preview + "\n\n[...truncated]"))
            console.print("=" * 70)

        else:
            console.print("\n[bold]Stage 5: LLM Execution[/bold]")

            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                console=console,
                transient=True,
            ) as progress:
                task = progress.add_task("Calling DeepSeek API...", total=None)

                # Get model from config
                agent_config = config.model_dump().get("agent", {})
                model = agent_config.get("model", "deepseek-chat")

                executor = LLMExecutor(
                    api_key=deepseek_key,
                    model=model,
                    output_dir=output_dir,
                )

                response, output = executor.execute(execution_context)

            console.print(f"  [green]✓[/green] LLM response received ({response.tokens_used} tokens)")
            console.print(f"  [green]✓[/green] Context: {output.context_file}")
            if output.selection_file:
                console.print(f"  [green]✓[/green] Selection: {output.selection_file}")
            console.print(f"  [green]✓[/green] Prompt: {output.prompt_file}")
            console.print(f"  [green]✓[/green] Reasoning: {output.reasoning_file}")
            console.print(f"  [green]✓[/green] Plan: {output.plan_file}")

            # Show work plan summary
            console.print("\n[bold]Work Plan Summary:[/bold]")
            console.print("=" * 70)

            if response.work_plan:
                console.print(Markdown(response.work_plan[:1500]))
            else:
                console.print("[yellow]Work plan section not found in response[/yellow]")

            console.print("=" * 70)

            # Show concerns if any
            if response.concerns:
                console.print("\n[bold yellow]Concerns & Uncertainties:[/bold yellow]")
                console.print(Markdown(response.concerns[:500]))

            # =================================================================
            # Post-Execution: Analysis & Decomposition + Transition
            # =================================================================
            console.print("\n[bold]Post-Execution: Analysis & Decomposition[/bold]")
            result = handle_post_execution(
                mcp=mcp,
                issue_key=issue_key,
                execution_context=execution_context,
                plan_summary=response.work_plan[:1000] if response.work_plan else None,
                llm_response=response,
                config=config.model_dump(),
                dry_run=False,
            )

            # Show decomposition results
            if result.decomposition_result:
                dr = result.decomposition_result
                console.print(f"  [green]✓[/green] Parsed {len(dr.stories)} stories")
                if dr.review_task_key:
                    console.print(f"  [green]✓[/green] Created review task: {dr.review_task_key}")
                else:
                    console.print(f"  [yellow]⚠[/yellow] Review task creation failed")
                if dr.has_questions():
                    console.print(f"  [yellow]![/yellow] {len(dr.questions)} clarifications needed")
                console.print(f"  [green]✓[/green] Added decomposition comments")

            if result.error:
                console.print(f"  [yellow]⚠[/yellow] Transition failed: {result.error}")
            else:
                console.print(f"  [green]✓[/green] Transitioned to {result.target_status}")

        console.print("\n[bold green]✓ Pipeline completed successfully[/bold green]")
        return 0

    except Exception as e:
        console.print(f"\n[bold red]✗ Error during execution: {e}[/bold red]")
        import traceback
        traceback.print_exc()
        return 1

    finally:
        console.print("\n[dim]Stopping MCP servers...[/dim]")
        mcp.stop_all()


def main():
    """Main CLI entry point."""
    parser = argparse.ArgumentParser(
        description="AI-SWARM Executor - 5-Stage SDLC Pipeline",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python3 execute.py --task PROJ-123
  python3 execute.py --task PROJ-123 --dry-run
  python3 execute.py -t PROJ-123 -o ./my_outputs

Stages:
  1. Trigger       - Parse Jira issue key
  2. Enrichment    - Extract Jira context
  3. Knowledge     - Retrieve Confluence data
  4. Aggregation   - Build unified context
  5. Execution     - Generate plan via DeepSeek LLM
        """
    )

    parser.add_argument(
        "--task", "-t",
        required=True,
        help="Jira issue key or URL"
    )

    parser.add_argument(
        "--dry-run", "-d",
        action="store_true",
        help="Skip LLM execution (Stages 1-4 only)"
    )

    parser.add_argument(
        "--output-dir", "-o",
        default="outputs",
        help="Output directory for generated files (default: outputs)"
    )

    args = parser.parse_args()

    try:
        return execute_pipeline(
            task_input=args.task,
            dry_run=args.dry_run,
            output_dir=args.output_dir,
        )

    except ValueError as e:
        console.print(f"[red]Error: {e}[/red]")
        return 1
    except KeyboardInterrupt:
        console.print("\n[yellow]Execution cancelled by user[/yellow]")
        return 130


if __name__ == "__main__":
    sys.exit(main())
