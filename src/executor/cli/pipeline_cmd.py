"""Full 5-stage pipeline command."""
import json
import os
import time
import logging
from pathlib import Path
from rich.panel import Panel
from rich.markdown import Markdown
from rich.progress import Progress, SpinnerColumn, TextColumn
from openai import OpenAI

from .shared import console, load_environment
from ..mcp.client import MCPClientManager
from ..utils.config_loader import load_config
from ..utils.structured_logging import new_trace_id, set_trace_id
from ..models.llm_metrics import PipelineMetrics, StageMetrics
from ..phases import (
    parse_issue_key,
    get_issue_status,
    build_context_pipeline,
    build_refined_context_pipeline,
    LLMExecutor,
    handle_post_execution,
    ExecutionOutcome,
    execute_phase_zero,
)
from ..phases.context_store import save_context
from ..phases.review_handler import human_plan_review_handler

logger = logging.getLogger(__name__)


def execute_pipeline(
    task_input: str,
    dry_run: bool = False,
    output_dir: str = "outputs",
    json_logs: bool = False,
    force: bool = False,
) -> tuple[int, dict]:
    """
    Execute the full 5-stage pipeline.

    Args:
        task_input: Jira issue key or URL
        dry_run: If True, skip LLM execution (Stages 1-4 only)
        output_dir: Directory for output files
        json_logs: If True, use JSON structured logging
        force: If True, bypass pre-flight artifact check

    Returns:
        Tuple of (exit_code, result_dict)
    """
    # Stage 1: Parse issue key
    issue_key = parse_issue_key(task_input)

    # Generate trace ID for this pipeline run (used in structured logs + JSON output)
    trace_id = new_trace_id()
    set_trace_id(trace_id)

    # Structured JSON result accumulator
    result: dict = {"trace_id": trace_id, "issue_key": issue_key, "status": "error", "output_files": {}}

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
    config_path = Path(__file__).parent.parent.parent.parent / "config" / "sdlc_config.yaml"
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
        from ..constants import fetch_link_types_from_confluence

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
            return 1, result

        # Route based on status
        status_lower = issue_status.lower().strip()

        if status_lower == "backlog":
            console.print(f"  [cyan]→[/cyan] Routing to Phase 0: Backlog Analysis")
            console.print("")

            if not deepseek_client:
                console.print("  [red]✗ Phase 0 requires DeepSeek API key[/red]")
                return 1, result

            agent_config = config.model_dump().get("agent", {})
            model = agent_config.get("model", "deepseek-chat")

            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                console=console,
                transient=True,
            ) as progress:
                task = progress.add_task("Phase 0: Building context & analyzing requirements...", total=None)

                phase0_result = execute_phase_zero(
                    mcp=mcp,
                    llm_client=deepseek_client,
                    issue_key=issue_key,
                    config=config.model_dump(),
                    output_dir=output_dir,
                    model=model,
                    dry_run=dry_run,
                )

            if phase0_result.error:
                console.print(f"\n[red]✗ Phase 0 failed: {phase0_result.error}[/red]")
                return 1, result

            resp = phase0_result.response
            console.print(f"  [green]✓[/green] LLM response ({resp.tokens_used} tokens)")
            console.print(f"  [green]✓[/green] Feature Type: {resp.feature_type}")
            console.print(f"  [green]✓[/green] Complexity: {resp.complexity_estimate}")

            if phase0_result.validation_errors:
                console.print(f"\n  [yellow]⚠ Validation warnings:[/yellow]")
                for err in phase0_result.validation_errors:
                    console.print(f"    - {err}")
            else:
                console.print(f"  [green]✓[/green] Validation passed")

            if phase0_result.output_file:
                console.print(f"  [green]✓[/green] Output: {phase0_result.output_file}")

            if phase0_result.jira_updated:
                console.print(f"  [green]✓[/green] Jira description updated for {issue_key}")
            elif dry_run:
                console.print(f"  [dim]  Jira update skipped (dry-run)[/dim]")

            if phase0_result.dor_met:
                console.print(f"\n  [bold green]✓ Definition of Ready: MET[/bold green]")
            else:
                console.print(f"\n  [bold yellow]⚠ Definition of Ready: NOT MET[/bold yellow]")

            if resp.chain_of_thought:
                console.print("\n[bold]Analysis Summary:[/bold]")
                console.print("=" * 70)
                console.print(Markdown(resp.chain_of_thought[:1500]))
                console.print("=" * 70)

            console.print("\n[bold green]✓ Phase 0 completed successfully[/bold green]")

            if phase0_result.dor_met and not dry_run:
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
                return 0, result

        # For non-Backlog, non-AI-TO-DO statuses → route appropriately
        if status_lower not in ("ai to do", "ai-to-do", "ai todo"):
            if status_lower == "human plan review":
                console.print(
                    "  [cyan]\u2192[/cyan] Routing to Plan Review handler"
                )
                console.print("")
                exit_code = human_plan_review_handler(
                    mcp=mcp,
                    issue_key=issue_key,
                    config=config.model_dump(),
                    output_dir=output_dir,
                )
                return exit_code, result

            from ..phases.status_checker import run_status_check
            exit_code = run_status_check(
                mcp=mcp,
                issue_key=issue_key,
                issue_status=issue_status,
                config=config.model_dump(),
                console=console,
                llm_client=deepseek_client,
                output_dir=output_dir,
                dry_run=dry_run,
            )
            return exit_code, result

        # =================================================================
        # Pre-flight: check artifacts for current status, create missing
        # =================================================================
        if not force and not dry_run:
            from ..phases.status_checker import (
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
                exit_code = run_status_check(
                    mcp=mcp,
                    issue_key=issue_key,
                    issue_status=issue_status,
                    config=config.model_dump(),
                    console=console,
                    llm_client=deepseek_client,
                    output_dir=output_dir,
                    dry_run=dry_run,
                )
                return exit_code, result

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
                post_result = handle_post_execution(
                    mcp=mcp,
                    issue_key=issue_key,
                    execution_context=execution_context,
                    execution_error=context_error,
                    dry_run=False,
                )
                if post_result.error:
                    console.print(f"  [yellow]⚠[/yellow] Transition failed: {post_result.error}")
                else:
                    console.print(f"  [green]✓[/green] Transitioned to {post_result.target_status}")
                    console.print(f"  [green]✓[/green] Added explanation comment")
            return 1, result

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
                post_result = handle_post_execution(
                    mcp=mcp,
                    issue_key=issue_key,
                    execution_context=execution_context,
                    dry_run=False,
                )
                if post_result.error:
                    console.print(f"  [yellow]⚠[/yellow] Transition failed: {post_result.error}")
                else:
                    console.print(f"  [green]✓[/green] Transitioned to {post_result.target_status}")
                    console.print(f"  [green]✓[/green] Added explanation comment")
            return 1, result

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
            result["output_files"] = {
                "plan": str(output.plan_file),
                "context": str(output.context_file),
                "prompt": str(output.prompt_file),
                "reasoning": str(output.reasoning_file),
            }
            if output.selection_file:
                result["output_files"]["selection"] = str(output.selection_file)

            # Show work plan summary
            console.print("\n[bold]Work Plan Summary:[/bold]")
            console.print("=" * 70)

            if response.work_plan:
                console.print(Markdown(response.work_plan[:1500]))
                result["work_plan"] = response.work_plan
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
            post_result = handle_post_execution(
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
            if post_result.decomposition_result:
                dr = post_result.decomposition_result
                console.print(f"  [green]✓[/green] Parsed {len(dr.stories)} stories")
                result["stories"] = [
                    {"title": s.title, "layer": s.layer, "story_points": s.story_points}
                    for s in dr.stories
                ]
                result["review_task_key"] = dr.review_task_key
                result["overall_confidence"] = dr.overall_confidence
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
                pipeline_metrics.validation_pass = post_result.outcome != ExecutionOutcome.EXECUTION_ERROR
                post_meta["stories"] = len(dr.stories)
                post_meta["confidence"] = f"{dr.overall_confidence:.0%}"

            if post_result.error:
                console.print(f"  [yellow]⚠[/yellow] Transition failed: {post_result.error}")
                pipeline_metrics.add_stage(StageMetrics(
                    stage_name="post_execution",
                    duration_ms=post_duration,
                    success=False,
                    error=post_result.error,
                    metadata=post_meta,
                ))
            else:
                console.print(f"  [green]✓[/green] Transitioned to {post_result.target_status}")
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
        result["status"] = "success"
        return 0, result

    except Exception as e:
        console.print(f"\n[bold red]✗ Error during execution: {e}[/bold red]")
        import traceback
        traceback.print_exc()
        result["error"] = str(e)
        return 1, result

    finally:
        console.print("\n[dim]Stopping MCP servers...[/dim]")
        mcp.stop_all()
