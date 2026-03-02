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
import json
import os
import sys
import time
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
from executor.constants import extract_project_key
from executor.utils.config_loader import load_config
from executor.utils.structured_logging import setup_structured_logging
from executor.models.llm_metrics import PipelineMetrics, StageMetrics
from executor.phases import (
    parse_issue_key,
    get_issue_status,
    build_context_pipeline,
    build_refined_context_pipeline,
    LLMExecutor,
    handle_post_execution,
    ExecutionOutcome,
    execute_phase_zero,
)
from executor.phases.context_store import save_context, load_context
from executor.utils.issue_lock import acquire_issue_lock, IssueLockError
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
    json_logs: bool = False,
    force: bool = False,
) -> int:
    """
    Execute the full 5-stage pipeline.

    Args:
        task_input: Jira issue key or URL
        dry_run: If True, skip LLM execution (Stages 1-4 only)
        output_dir: Directory for output files
        json_logs: If True, use JSON structured logging
        force: If True, bypass pre-flight artifact check

    Returns:
        Exit code (0 = success, 1 = error)
    """
    # Stage 1: Parse issue key
    issue_key = parse_issue_key(task_input)

    # Initialize pipeline metrics
    pipeline_metrics = PipelineMetrics(issue_key=issue_key)

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

        # Enrich config with link types from Confluence SDLC page
        from executor.constants import fetch_link_types_from_confluence

        link_overrides = fetch_link_types_from_confluence(mcp, config.model_dump())
        if link_overrides:
            config.jira.update(link_overrides)
            console.print(
                "  [green]✓[/green] Link types loaded from Confluence: "
                + ", ".join(f"{k}={v}" for k, v in link_overrides.items())
            )

        # =================================================================
        # Stage 1.5: Auto-detect issue status and route to correct phase
        # =================================================================
        console.print("\n[bold]Stage 1: Status Detection[/bold]")
        try:
            issue_status = get_issue_status(mcp, issue_key)
            console.print(f"  [green]✓[/green] Issue status: {issue_status}")
        except Exception as e:
            console.print(f"  [red]✗ Failed to get issue status: {e}[/red]")
            return 1

        # Route based on status
        status_lower = issue_status.lower().strip()

        if status_lower == "backlog":
            console.print(f"  [cyan]→[/cyan] Routing to Phase 0: Backlog Analysis")
            console.print("")

            if not deepseek_client:
                console.print("  [red]✗ Phase 0 requires DeepSeek API key[/red]")
                return 1

            agent_config = config.model_dump().get("agent", {})
            model = agent_config.get("model", "deepseek-chat")

            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                console=console,
                transient=True,
            ) as progress:
                task = progress.add_task("Phase 0: Building context & analyzing requirements...", total=None)

                result = execute_phase_zero(
                    mcp=mcp,
                    llm_client=deepseek_client,
                    issue_key=issue_key,
                    config=config.model_dump(),
                    output_dir=output_dir,
                    model=model,
                    dry_run=dry_run,
                )

            if result.error:
                console.print(f"\n[red]✗ Phase 0 failed: {result.error}[/red]")
                return 1

            resp = result.response
            console.print(f"  [green]✓[/green] LLM response ({resp.tokens_used} tokens)")
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

            if result.dor_met:
                console.print(f"\n  [bold green]✓ Definition of Ready: MET[/bold green]")
            else:
                console.print(f"\n  [bold yellow]⚠ Definition of Ready: NOT MET[/bold yellow]")

            if resp.chain_of_thought:
                console.print("\n[bold]Analysis Summary:[/bold]")
                console.print("=" * 70)
                console.print(Markdown(resp.chain_of_thought[:1500]))
                console.print("=" * 70)

            console.print("\n[bold green]✓ Phase 0 completed successfully[/bold green]")

            if result.dor_met and not dry_run:
                # DoR met → issue transitioned to AI-TO-DO
                # No artifacts exist yet → must run full pipeline (Stages 2-5)
                console.print(
                    "\n[bold cyan]→ DoR met — running full pipeline"
                    " to create AI-TO-DO artifacts[/bold cyan]"
                )
                issue_status = "AI-TO-DO"
                status_lower = "ai-to-do"
                force = True  # bypass pre-flight — nothing exists yet
                # Fall through to Stages 2-5 below
            else:
                return 0

        # For non-Backlog, non-AI-TO-DO statuses → route appropriately
        if status_lower not in ("ai to do", "ai-to-do", "ai todo"):
            if status_lower == "human plan review":
                console.print(
                    "  [cyan]\u2192[/cyan] Routing to Plan Review handler"
                )
                console.print("")
                return human_plan_review_handler(
                    mcp=mcp,
                    issue_key=issue_key,
                    config=config.model_dump(),
                    output_dir=output_dir,
                )

            from executor.phases.status_checker import run_status_check
            return run_status_check(
                mcp=mcp,
                issue_key=issue_key,
                issue_status=issue_status,
                config=config.model_dump(),
                console=console,
                llm_client=deepseek_client,
                output_dir=output_dir,
                dry_run=dry_run,
            )

        # =================================================================
        # Pre-flight: check artifacts for current status, create missing
        # =================================================================
        if not force and not dry_run:
            from executor.phases.status_checker import (
                preflight_check,
                _render_checklist_table,
                run_status_check,
            )

            console.print("\n[bold]Pre-flight: Checking artifacts for status"
                          f" '{issue_status}'[/bold]")
            preflight_items = preflight_check(
                mcp=mcp,
                issue_key=issue_key,
                issue_status=issue_status,
            )

            _render_checklist_table(console, preflight_items)
            all_fulfilled = all(item.passed for item in preflight_items)

            if all_fulfilled:
                console.print(
                    "\n[bold cyan]All artifacts present[/bold cyan]"
                )
            else:
                missing = [i.name for i in preflight_items if not i.passed]
                console.print(
                    f"\n[dim]Missing: {', '.join(missing)}"
                    f" -- will create missing artifacts[/dim]"
                )

            # AI-TO-DO → always fall through to Stages 2-5
            if status_lower in ("ai to do", "ai-to-do", "ai todo"):
                if all_fulfilled:
                    console.print(
                        "[bold yellow]Artifacts from a previous run found — "
                        "they will be overwritten by the new pipeline run[/bold yellow]"
                    )
                # Fall through to Stages 2-5 regardless of all_fulfilled
            else:
                console.print(
                    "[dim]  (use --force to re-run the full pipeline)[/dim]"
                )
                return run_status_check(
                    mcp=mcp,
                    issue_key=issue_key,
                    issue_status=issue_status,
                    config=config.model_dump(),
                    console=console,
                    llm_client=deepseek_client,
                    output_dir=output_dir,
                    dry_run=dry_run,
                )

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
                context_start = time.time()
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

                context_duration = int((time.time() - context_start) * 1000)
                pipeline_metrics.add_stage(StageMetrics(
                    stage_name="context_building",
                    duration_ms=context_duration,
                ))
            except Exception as e:
                context_error = e
                context_duration = int((time.time() - context_start) * 1000)
                pipeline_metrics.add_stage(StageMetrics(
                    stage_name="context_building",
                    duration_ms=context_duration,
                    success=False,
                    error=str(e),
                ))
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

        # Save context for future refinement (before Stage 5)
        save_context(execution_context, output_dir)
        console.print(f"  [green]✓[/green] Context saved for refinement")

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

            llm_start = time.time()
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

            llm_duration = int((time.time() - llm_start) * 1000)
            pipeline_metrics.add_stage(StageMetrics(
                stage_name="llm_execution",
                duration_ms=llm_duration,
                metadata={"tokens": response.tokens_used, "model": model},
            ))
            pipeline_metrics.llm_metrics = executor.metrics

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
            post_start = time.time()
            result = handle_post_execution(
                mcp=mcp,
                issue_key=issue_key,
                execution_context=execution_context,
                plan_summary=response.work_plan[:1000] if response.work_plan else None,
                llm_response=response,
                config=config.model_dump(),
                dry_run=False,
            )
            post_duration = int((time.time() - post_start) * 1000)

            post_meta = {}
            # Show decomposition results
            if result.decomposition_result:
                dr = result.decomposition_result
                console.print(f"  [green]✓[/green] Parsed {len(dr.stories)} stories")
                # Show confidence
                conf_pct = f"{dr.overall_confidence:.0%}"
                if dr.overall_confidence >= 0.7:
                    console.print(f"  [green]✓[/green] Overall confidence: {conf_pct}")
                else:
                    console.print(f"  [yellow]⚠[/yellow] Overall confidence: {conf_pct} (below threshold)")
                if dr.low_confidence_stories:
                    console.print(f"  [yellow]⚠[/yellow] {len(dr.low_confidence_stories)} stories below confidence threshold")
                if dr.review_task_key:
                    console.print(f"  [green]✓[/green] Created PLAN REVIEW task: {dr.review_task_key}")
                else:
                    console.print(f"  [yellow]⚠[/yellow] PLAN REVIEW task creation failed")
                if dr.has_questions():
                    console.print(f"  [yellow]![/yellow] {len(dr.questions)} clarifications needed")
                console.print(f"  [green]✓[/green] Added decomposition comments")

                # Populate pipeline metrics from decomposition
                pipeline_metrics.stories_extracted = len(dr.stories)
                pipeline_metrics.stories_created = 1 if dr.review_task_key else 0
                pipeline_metrics.overall_confidence = dr.overall_confidence
                pipeline_metrics.validation_pass = result.outcome != ExecutionOutcome.EXECUTION_ERROR
                post_meta["stories"] = len(dr.stories)
                post_meta["confidence"] = f"{dr.overall_confidence:.0%}"

            if result.error:
                console.print(f"  [yellow]⚠[/yellow] Transition failed: {result.error}")
                pipeline_metrics.add_stage(StageMetrics(
                    stage_name="post_execution",
                    duration_ms=post_duration,
                    success=False,
                    error=result.error,
                    metadata=post_meta,
                ))
            else:
                console.print(f"  [green]✓[/green] Transitioned to {result.target_status}")
                pipeline_metrics.add_stage(StageMetrics(
                    stage_name="post_execution",
                    duration_ms=post_duration,
                    metadata=post_meta,
                ))

            # Finalize and save pipeline metrics
            pipeline_metrics.finalize()

            issue_dir = Path(output_dir) / issue_key
            issue_dir.mkdir(parents=True, exist_ok=True)

            metrics_md_path = issue_dir / f"{issue_key}_pipeline_metrics.md"
            metrics_md_path.write_text(pipeline_metrics.to_markdown(), encoding="utf-8")
            console.print(f"  [green]✓[/green] Metrics: {metrics_md_path}")

            if json_logs:
                metrics_json_path = issue_dir / f"{issue_key}_pipeline_metrics.json"
                metrics_json_path.write_text(
                    json.dumps(pipeline_metrics.to_json(), indent=2, ensure_ascii=False),
                    encoding="utf-8",
                )
                console.print(f"  [green]✓[/green] Metrics JSON: {metrics_json_path}")

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


def human_plan_review_handler(
    mcp: MCPClientManager,
    issue_key: str,
    config: dict,
    output_dir: str = "outputs",
) -> int:
    """
    Handle --task on an issue in "Human Plan Review" status.

    1. Check if [PLAN REVIEW] task is Done
    2. If not Done -> inform user, return 0
    3. If Done -> create stories -> transition to "Ready for Dev"

    Args:
        mcp: MCP client manager
        issue_key: Jira issue key
        config: SDLC config dict
        output_dir: Output directory for manifest file

    Returns:
        Exit code: 0 = success or waiting, 1 = error, 2 = partial story creation
    """
    from executor.phases.story_creator import (
        check_review_approved,
        ensure_review_blocking_link,
        extract_stories_from_comment,
        create_jira_stories,
        create_dependency_links,
    )

    project_key = extract_project_key(issue_key)

    # Step 1: Check [PLAN REVIEW] status
    console.print("[bold]Step 1: Checking review approval[/bold]")
    approved, review_key = check_review_approved(mcp, issue_key)

    if review_key is None:
        console.print(f"[red]\u2717 No [PLAN REVIEW] task found for {issue_key}.[/red]")
        console.print(f"   This may indicate the pipeline didn't complete properly.")
        console.print(
            f"   Consider re-running: python execute.py --task {issue_key} --force"
        )
        return 1

    if not approved:
        console.print(
            f"[yellow]\u23f3 [PLAN REVIEW] for {issue_key} is not yet Done.[/yellow]"
        )
        console.print(f"   Review task: {review_key}")
        console.print(
            f"   Waiting for human approval. Re-run --task after review is complete."
        )
        return 0

    console.print(f"  [green]\u2713[/green] PLAN REVIEW task {review_key} is approved")

    # Ensure blocking link (Review blocks Feature)
    if ensure_review_blocking_link(mcp, review_key, issue_key, config):
        console.print(
            f"  [green]\u2713[/green] {issue_key} is blocked by {review_key}"
        )

    # Step 2: Extract and create stories
    console.print("\n[bold]Step 2: Extracting stories from decomposition[/bold]")
    stories = extract_stories_from_comment(mcp, issue_key)

    if not stories:
        console.print("  [red]\u2717 No stories found in decomposition comment[/red]")
        return 1

    console.print(f"  [green]\u2713[/green] Found {len(stories)} stories")
    for story in stories:
        console.print(f"    {story.order}. [{story.layer}] {story.title}")

    console.print(f"\n[bold]Step 3: Creating Jira Stories in {project_key}[/bold]")
    report = create_jira_stories(
        mcp=mcp,
        parent_key=issue_key,
        project_key=project_key,
        stories=stories,
        config=config,
        output_dir=output_dir,
    )

    # Create dependency links for created + skipped stories
    all_existing = report.created_or_skipped
    has_deps = any(story.depends_on for story, _ in all_existing)
    if has_deps:
        console.print("\n[bold]Step 3.1: Creating dependency links[/bold]")
        dep_count = create_dependency_links(mcp, all_existing, config)
        console.print(f"  [green]\u2713[/green] Created {dep_count} dependency links")

    # Report results
    console.print(f"\n[bold]Results:[/bold]")
    for outcome in report.outcomes:
        key = outcome.jira_key or "N/A"
        layer = outcome.story.layer
        title = outcome.story.title

        if outcome.status == "created":
            deps_str = ""
            if outcome.story.depends_on:
                order_to_key = {s.order: k for s, k in all_existing}
                dep_keys = [
                    order_to_key.get(d, f"Step {d}") for d in outcome.story.depends_on
                ]
                deps_str = f" (blocked by: {', '.join(dep_keys)})"
            console.print(f"  [green]\u2713[/green] {key}: [{layer}] {title}{deps_str}")
        elif outcome.status == "skipped":
            console.print(f"  [dim]\u21a9 {key}: [{layer}] {title} (already exists)[/dim]")
        else:
            console.print(f"  [red]\u2717[/red] [{layer}] {title}: {outcome.error}")

    # Evaluate results and determine exit code
    n_created = len(report.created)
    n_skipped = len(report.skipped)
    n_failed = len(report.failed)
    total = len(stories)

    if n_failed == total:
        console.print(f"\n[bold red]\u2717 All {n_failed} stories failed to create[/bold red]")
        return 1

    if n_failed > 0:
        console.print(
            f"\n[bold yellow]\u26a0 Partial success: {n_created} created, "
            f"{n_skipped} skipped, {n_failed} failed.[/bold yellow]"
        )
        console.print(
            f"  Re-run `--task {issue_key}` to retry failed stories."
        )
        return 2  # Partial — do NOT transition

    # All created or skipped — proceed to transition
    if n_skipped > 0:
        console.print(
            f"\n[bold green]\u2713 {n_created} created, {n_skipped} skipped "
            f"(already existed) \u2014 {total} total[/bold green]"
        )
    else:
        console.print(f"\n[bold green]\u2713 Created {n_created}/{total} stories[/bold green]")

    # Step 4: Transition to "Ready for Dev"
    target_status = config.get("jira", {}).get("statuses", {}).get(
        "ready_for_dev", "Ready for Dev"
    )
    console.print(f"\n[bold]Step 4: Transitioning to {target_status}[/bold]")

    try:
        mcp.jira_transition_issue(issue_key, target_status)
    except Exception as e:
        logger.error(f"Transition to '{target_status}' failed: {e}")
        console.print(f"  [red]\u2717 Transition failed: {e}[/red]")
        return 1

    # Post-transition verification
    try:
        current = get_issue_status(mcp, issue_key)
        if current.lower() != target_status.lower():
            logger.error(
                f"Transition verification failed: expected '{target_status}', got '{current}'"
            )
            console.print(
                f"  [red]\u2717 Verification failed: expected '{target_status}', "
                f"got '{current}'[/red]"
            )
            return 1
    except Exception as e:
        logger.warning(f"Could not verify transition: {e}")

    console.print(f"  [green]\u2713[/green] {issue_key} \u2192 {target_status}")
    return 0






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
    config_path = Path(__file__).parent / "config" / "sdlc_config.yaml"
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
    config_path = Path(__file__).parent / "config" / "sdlc_config.yaml"
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


def main():
    """Main CLI entry point."""
    parser = argparse.ArgumentParser(
        description="AI-SWARM Executor - 5-Stage SDLC Pipeline",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python3 execute.py --phase0 PROJ-123             # Phase 0: Backlog Analysis
  python3 execute.py --phase0 PROJ-123 --dry-run   # Phase 0: skip Jira comment
  python3 execute.py --task PROJ-123               # Full pipeline (Stages 1-5)
  python3 execute.py --task PROJ-123 --dry-run
  python3 execute.py --task PROJ-123 --json-logs
  python3 execute.py --refine PROJ-123 --feedback "Split step 3 into BE and FE"
  python3 execute.py -t PROJ-123 -o ./my_outputs

Phases:
  0. Phase 0       - Backlog Analysis (Use Case + DoR)

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
        help="Jira issue key or URL (for full pipeline)"
    )

    parser.add_argument(
        "--phase0",
        metavar="ISSUE_KEY",
        help="Run Phase 0: Backlog Analysis (Use Case + DoR generation)"
    )

    parser.add_argument(
        "--refine",
        metavar="ISSUE_KEY",
        help="Refine a previous plan with human feedback"
    )

    parser.add_argument(
        "--feedback",
        help="Feedback text for --refine mode (required with --refine)"
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

    parser.add_argument(
        "--json-logs",
        action="store_true",
        help="Use JSON structured logging (for CI/automation)"
    )

    parser.add_argument(
        "--force", "-f",
        action="store_true",
        help="Force full pipeline re-execution, bypassing pre-flight artifact check"
    )

    args = parser.parse_args()

    # Configure structured logging if requested
    if args.json_logs:
        setup_structured_logging(level="INFO", json_output=True)

    # Dispatch to appropriate pipeline
    if args.phase0:
        try:
            issue_key = parse_issue_key(args.phase0)
            with acquire_issue_lock(issue_key):
                return phase_zero_pipeline(
                    task_input=args.phase0,
                    dry_run=args.dry_run,
                    output_dir=args.output_dir,
                )
        except IssueLockError as e:
            console.print(f"[red]Error: {e}[/red]")
            return 1
        except ValueError as e:
            console.print(f"[red]Error: {e}[/red]")
            return 1
        except KeyboardInterrupt:
            console.print("\n[yellow]Cancelled by user[/yellow]")
            return 130

    if args.refine:
        if not args.feedback:
            parser.error("--feedback is required with --refine")
        try:
            issue_key = parse_issue_key(args.refine)
            with acquire_issue_lock(issue_key):
                return refinement_pipeline(
                    task_input=args.refine,
                    feedback=args.feedback,
                    output_dir=args.output_dir,
                    json_logs=args.json_logs,
                )
        except IssueLockError as e:
            console.print(f"[red]Error: {e}[/red]")
            return 1
        except ValueError as e:
            console.print(f"[red]Error: {e}[/red]")
            return 1
        except KeyboardInterrupt:
            console.print("\n[yellow]Cancelled by user[/yellow]")
            return 130

    if not args.task:
        parser.error("--task is required (or use --phase0 / --refine)")

    try:
        issue_key = parse_issue_key(args.task)
        with acquire_issue_lock(issue_key):
            return execute_pipeline(
                task_input=args.task,
                dry_run=args.dry_run,
                output_dir=args.output_dir,
                json_logs=args.json_logs,
                force=args.force,
            )

    except IssueLockError as e:
        console.print(f"[red]Error: {e}[/red]")
        return 1
    except ValueError as e:
        console.print(f"[red]Error: {e}[/red]")
        return 1
    except KeyboardInterrupt:
        console.print("\n[yellow]Execution cancelled by user[/yellow]")
        return 130


if __name__ == "__main__":
    sys.exit(main())
