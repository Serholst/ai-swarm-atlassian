"""Human Plan Review handler - routes Human Plan Review status issues."""
import logging
from pathlib import Path
from rich.console import Console

from ..constants import extract_project_key
from . import get_issue_status

logger = logging.getLogger(__name__)
console = Console()


def human_plan_review_handler(mcp, issue_key: str, config: dict, output_dir: str = "outputs") -> int:
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
    from .story_creator import (
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
