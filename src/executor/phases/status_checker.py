"""
Status-based checklist validation for already-processed Jira issues.

Called by execute.py when `--task` is run on an issue whose status is
downstream of AI-TO-DO (e.g. Human Plan Review, Ready for Dev).

Instead of re-running the full LLM pipeline, validates that the required
artefacts for the current status exist and auto-fixes missing ones.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Optional

from ..constants import LAYER_RE, validate_issue_key, extract_project_key, get_blocking_link_type
from ..mcp.client import MCPClientManager

logger = logging.getLogger(__name__)


# ── Data model ────────────────────────────────────────────────────────────────

@dataclass
class CheckItem:
    name: str
    passed: bool
    details: str
    fixed: bool = False
    fix_error: Optional[str] = None


# ── Comment block constants ───────────────────────────────────────────────────

# Ordered titles of the 4 expand blocks written by build_consolidated_adf_comment()
COMMENT_BLOCKS: list[str] = [
    "Context Summary",
    "Technical Decomposition",
    "Executor Rationale",
    "Clarification Questions",   # optional — only present when there are questions
]
REQUIRED_BLOCKS: frozenset[str] = frozenset({
    "Context Summary",
    "Technical Decomposition",
    "Executor Rationale",
})
_MIN_BLOCK_CHARS = 30  # anything shorter is treated as empty / not populated


# ── Helpers ───────────────────────────────────────────────────────────────────

def _search_keys(mcp: MCPClientManager, jql: str) -> list[str]:
    """Run JQL search and return list of found issue keys."""
    result = mcp.jira_search_issues(jql, max_results=20)
    # Parses **KEY-123** from jira_search_issues markdown output format
    # (see jira_server.py call_tool "jira_search_issues" handler)
    return re.findall(r"\*\*([A-Z][A-Z0-9]*-\d+)\*\*", result)


def _get_linked_keys(mcp: MCPClientManager, issue_key: str) -> list[str]:
    """Return keys of all issues linked to issue_key (any link type).

    Uses direct REST API (issuelinks field) — does not depend on JQL search index.
    """
    try:
        links = mcp.jira_get_issue_links(issue_key)
        return [link["key"] for link in links if link.get("key")]
    except Exception:
        # Fallback to JQL if direct API fails
        return _search_keys(mcp, f'issue in linkedIssues("{issue_key}")')


def _find_review_in_linked(mcp: MCPClientManager, issue_key: str) -> Optional[str]:
    """Find a [PLAN REVIEW] task among linked issues.

    Uses direct REST API (issuelinks field) which returns summaries inline —
    no extra API calls needed. Does not depend on JQL search index.
    """
    try:
        links = mcp.jira_get_issue_links(issue_key)
        for link in links:
            summary = link.get("summary", "")
            if "[PLAN REVIEW]" in summary or "[REVIEW]" in summary:
                return link["key"]
    except Exception:
        logger.debug(f"Direct issuelinks lookup failed for {issue_key}, falling back to JQL")
        # Fallback: JQL search + per-issue fetch
        keys = _search_keys(mcp, f'issue in linkedIssues("{issue_key}")')
        for key in keys:
            try:
                issue_text = mcp.jira_get_issue(key)
                first_line = (issue_text.split("\n", 1)[0] if issue_text else "")
                if "[PLAN REVIEW]" in first_line or "[REVIEW]" in first_line:
                    return key
            except Exception:
                continue
    return None


def _parse_comment_blocks(comments_text: str) -> dict[str, str]:
    """
    Extract the content of each expand block from the rendered ADF comment text.

    ADF expand nodes render as:  "{title}\\n{content}\\n"
    (see jira_server.py extract_adf_text, node_type == "expand")

    Returns dict of {block_title: content_str} for every block found.
    """
    blocks: dict[str, str] = {}
    for i, title in enumerate(COMMENT_BLOCKS):
        start = comments_text.find(title)
        if start == -1:
            continue
        content_start = start + len(title) + 1   # skip title + newline
        # End content at the next known block title (or end of string)
        end = len(comments_text)
        for other in COMMENT_BLOCKS[i + 1:]:
            pos = comments_text.find(other, content_start)
            if pos != -1 and pos < end:
                end = pos
        blocks[title] = comments_text[content_start:end].strip()
    return blocks


def _regenerate_comment(
    mcp: MCPClientManager,
    issue_key: str,
    llm_client,
    output_dir: str,
    config: dict,
) -> tuple[bool, str]:
    """
    Regenerate the consolidated AI Executor comment by re-running Stage 5.

    Loads saved ExecutionContext from context_store, calls the LLM,
    then posts the rebuilt ADF comment to the Jira issue.

    Returns:
        (success, message)
    """
    from .context_store import load_context
    from .llm_executor import LLMExecutor
    from .decomposition import parse_llm_response, build_consolidated_adf_comment

    context = load_context(issue_key, output_dir)
    if not context:
        return False, (
            f"Context store not found at outputs/{issue_key}/ "
            "— run the full pipeline first to save context"
        )

    model = config.get("agent", {}).get("model", "deepseek-chat")
    temperature = config.get("agent", {}).get("temperature", 0.2)
    max_tokens = config.get("agent", {}).get("max_tokens", 8192)

    try:
        executor = LLMExecutor(
            api_key=None,   # picks up DEEPSEEK_API_KEY from env
            model=model,
            output_dir=output_dir,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        llm_response, _ = executor.execute(context)
    except Exception as exc:
        return False, f"LLM call failed: {exc}"

    result = parse_llm_response(
        response=llm_response,
        issue_key=issue_key,
        feature_title=context.jira.summary,
        execution_context=context,
    )

    adf = build_consolidated_adf_comment(
        result=result,
        parent_key=issue_key,
        execution_context=context,
        plan_summary=llm_response.work_plan[:1500] if llm_response.work_plan else None,
    )

    try:
        mcp.jira_add_comment_adf(issue_key, adf)
        return True, f"comment regenerated ({len(result.stories)} stories)"
    except Exception as exc:
        return False, f"Failed to post regenerated comment: {exc}"


# ── Checkers ─────────────────────────────────────────────────────────────────

def check_human_plan_review(
    mcp: MCPClientManager,
    issue_key: str,
    project_key: str,
    config: dict,
    known_review_key: Optional[str] = None,
) -> list[CheckItem]:
    """
    Validate Acceptance Criteria for 'Human Plan Review' status:

    1. PLAN REVIEW task exists             → auto-create if missing
    2. Blocks link exists             → auto-create if missing
    3. Work plan comment — per block:
       a. Context Summary             → regenerate via LLM if missing/empty
       b. Technical Decomposition     → regenerate via LLM if missing/empty
       c. Executor Rationale          → regenerate via LLM if missing/empty
       d. Clarification Questions     → optional, report-only
    4. Child stories linked           → report-only
    """
    results: list[CheckItem] = []
    blocking_link_type = get_blocking_link_type(config)

    # ── 1. PLAN REVIEW task exists ─────────────────────────────────────────────────
    # Use known_review_key if the dispatcher already found one, otherwise search.
    # JQL text search (summary ~) is unreliable with square brackets / hyphens,
    # so we fall back to scanning linked issues directly.
    review_key = known_review_key
    if not review_key:
        review_key = _find_review_in_linked(mcp, issue_key)

    review_item = CheckItem(
        "PLAN REVIEW task exists",
        bool(review_key),
        review_key or "not found",
    )

    if not review_key:
        try:
            from .decomposition import create_blocking_review_task
            review_key = create_blocking_review_task(mcp, project_key, issue_key, config)
            if review_key:
                review_item.fixed = True
                review_item.passed = True
                review_item.details = f"created → {review_key}"
            else:
                review_item.fix_error = "create_blocking_review_task returned None"
        except Exception as exc:
            review_item.fix_error = str(exc)

    results.append(review_item)

    # ── 2. Blocks link exists ─────────────────────────────────────────────────
    if review_key:
        # Check via direct API — look for a blocking link between issue and review
        has_link = False
        try:
            links = mcp.jira_get_issue_links(issue_key)
            has_link = any(
                link["key"] == review_key and "block" in link.get("link_type", "").lower()
                for link in links
            )
        except Exception:
            pass
        link_item = CheckItem(
            "Blocks link exists",
            has_link,
            f"{issue_key} is blocked by {review_key}" if has_link else "missing",
        )
        if not has_link:
            try:
                mcp.jira_link_issues(
                    from_key=issue_key,
                    to_key=review_key,
                    link_type=blocking_link_type,
                )
                link_item.fixed = True
                link_item.passed = True
                link_item.details = (
                    f"created: {issue_key} →[{blocking_link_type}]→ {review_key}"
                )
            except Exception as exc:
                link_item.fix_error = str(exc)
        results.append(link_item)

    # ── 3. Work plan comment — per-block verification ────────────────────────
    comments_text = mcp.jira_get_comments(issue_key)
    blocks = _parse_comment_blocks(comments_text)

    missing_required = [
        b for b in ["Context Summary", "Technical Decomposition", "Executor Rationale"]
        if b not in blocks or len(blocks.get(b, "")) < _MIN_BLOCK_CHARS
    ]

    # Per-block status items (shown individually in the table)
    for title in COMMENT_BLOCKS:
        content = blocks.get(title, "")
        is_optional = title not in REQUIRED_BLOCKS
        has_content = bool(content) and len(content) >= _MIN_BLOCK_CHARS

        if is_optional and title not in blocks:
            results.append(CheckItem(
                f"  └ {title}",
                True,
                "not present (optional — only added when there are questions)",
            ))
        else:
            results.append(CheckItem(
                f"  └ {title}",
                has_content,
                f"{len(content)} chars" if has_content else "missing or empty",
            ))

    if missing_required:
        results.append(CheckItem(
            "Work plan comment",
            False,
            f"missing: {', '.join(missing_required)} — run with --force to generate",
        ))

    # Child stories are NOT checked here — they belong to Ready for Dev
    # (handled by check_ready_for_dev_promotion)

    return results


# ── Ready-for-Dev promotion ──────────────────────────────────────────────────

def check_ready_for_dev_promotion(
    mcp: MCPClientManager,
    issue_key: str,
    project_key: str,
    config: dict,
    dry_run: bool = False,
) -> tuple[list[CheckItem], list[tuple]]:
    """
    Validate promotion criteria for 'Human Plan Review' → 'Ready for Dev'.

    Called when the [PLAN REVIEW] task is Done.  Runs a full checklist:
    1. PLAN REVIEW task exists and is Done
    2. Blocks link exists
    3. Required comment blocks present (Context Summary, Technical Decomposition,
       Executor Rationale)
    4. No unresolved BLOCKING clarification questions
    5. Each story in Technical Decomposition has non-empty Acceptance Criteria
    6. Create stories (idempotent — skip if already exist)

    Returns:
        (check_items, created_stories)
    """
    from .story_creator import (
        check_review_approved,
        extract_stories_from_comment,
        create_jira_stories,
        create_dependency_links,
    )

    results: list[CheckItem] = []
    created_stories: list[tuple] = []

    # ── 1-2. PLAN REVIEW task exists and is Done ──────────────────────────────────
    is_done, review_key = check_review_approved(mcp, issue_key)

    results.append(CheckItem(
        "PLAN REVIEW task exists",
        bool(review_key),
        review_key or "not found",
    ))
    results.append(CheckItem(
        "PLAN REVIEW task is Done",
        is_done,
        f"{review_key} is Done" if is_done else f"{review_key or 'N/A'} is NOT Done",
    ))

    if not is_done:
        return results, created_stories

    # ── 3. Blocks link exists (correct direction: review blocks feature) ────
    has_blocking_link = False
    try:
        links = mcp.jira_get_issue_links(issue_key)
        has_blocking_link = any(
            link["key"] == review_key and "block" in link.get("link_type", "").lower()
            for link in links
        )
    except Exception:
        pass
    results.append(CheckItem(
        "Blocks link exists",
        has_blocking_link,
        f"{review_key} blocks {issue_key}" if has_blocking_link else "missing",
    ))

    # ── 4. Required comment blocks ───────────────────────────────────────────
    comments_text = mcp.jira_get_comments(issue_key)
    blocks = _parse_comment_blocks(comments_text)

    for title in REQUIRED_BLOCKS:
        content = blocks.get(title, "")
        has_content = bool(content) and len(content) >= _MIN_BLOCK_CHARS
        results.append(CheckItem(
            f"Comment: {title}",
            has_content,
            f"{len(content)} chars" if has_content else "missing or empty",
        ))

    # ── 5. No unresolved BLOCKING questions ──────────────────────────────────
    clarification = blocks.get("Clarification Questions", "")
    has_blocking = bool(re.search(r"BLOCKING", clarification, re.IGNORECASE)) if clarification else False
    results.append(CheckItem(
        "No unresolved BLOCKING questions",
        not has_blocking,
        "unresolved BLOCKING questions found" if has_blocking else "clear",
    ))

    # ── 6. Each story has Acceptance Criteria ────────────────────────────────
    stories = extract_stories_from_comment(mcp, issue_key)
    stories_without_ac = [
        f"[{s.layer}] {s.title}"
        for s in stories
        if not s.acceptance or len(s.acceptance.strip()) < 10
    ]
    results.append(CheckItem(
        "Stories have Acceptance Criteria",
        len(stories_without_ac) == 0 and len(stories) > 0,
        (
            f"{len(stories)} stories, all with AC"
            if stories and not stories_without_ac
            else f"missing AC: {', '.join(stories_without_ac[:3])}"
            if stories_without_ac
            else "no stories found in decomposition"
        ),
    ))

    # ── 7. Child stories — idempotent creation ───────────────────────────────
    all_linked = _get_linked_keys(mcp, issue_key)
    existing_child_stories: list[str] = []
    for key in all_linked:
        if key == review_key:
            continue
        try:
            issue_text = mcp.jira_get_issue(key)
            if LAYER_RE.search(issue_text.split("\n", 1)[0] if issue_text else ""):
                existing_child_stories.append(key)
        except Exception:
            pass

    if existing_child_stories:
        results.append(CheckItem(
            "Child stories",
            True,
            f"already exist: {', '.join(existing_child_stories)} (skipping creation)",
        ))
    elif stories and not dry_run:
        created_stories = create_jira_stories(
            mcp=mcp,
            parent_key=issue_key,
            project_key=project_key,
            stories=stories,
            config=config,
        )
        dep_count = create_dependency_links(mcp, created_stories, config)
        results.append(CheckItem(
            "Child stories created",
            len(created_stories) == len(stories),
            f"created {len(created_stories)}/{len(stories)} stories, {dep_count} dep links",
        ))
    elif dry_run:
        results.append(CheckItem(
            "Child stories",
            True,
            f"dry-run: would create {len(stories)} stories",
        ))
    else:
        results.append(CheckItem(
            "Child stories",
            False,
            "no stories found — cannot create",
        ))

    return results, created_stories


# ── Pre-flight fulfillment check ──────────────────────────────────────────────

def preflight_check(
    mcp: MCPClientManager,
    issue_key: str,
    issue_status: str,
) -> list[CheckItem]:
    """
    Cascading pre-flight check — Jira-only, no local files.

    Each status includes all checks from the previous status plus its own:

    Backlog        → description exists
    AI-TO-DO       → Backlog + 3 comment blocks + [PLAN REVIEW] task + blocking link
    Human Plan Rev → AI-TO-DO + [PLAN REVIEW] is Done
    Ready for Dev  → Human Plan Rev + child stories with links

    Returns:
        List of CheckItems. Items that fail can be auto-fixed by run_status_check.
    """
    results: list[CheckItem] = []
    status = issue_status.lower().strip()

    # ── Backlog: description exists ──────────────────────────────────────────
    issue_text = mcp.jira_get_issue(issue_key)
    has_description = bool(issue_text) and len(issue_text.strip()) > 50
    results.append(CheckItem(
        "Description exists",
        has_description,
        f"{len(issue_text)} chars" if has_description else "missing or too short",
    ))

    if status == "backlog":
        return results

    # ── AI-TO-DO: comment blocks + PLAN REVIEW + blocking link ───────────────
    comments_text = mcp.jira_get_comments(issue_key)
    blocks = _parse_comment_blocks(comments_text)

    for title in sorted(REQUIRED_BLOCKS):
        content = blocks.get(title, "")
        has_content = bool(content) and len(content) >= _MIN_BLOCK_CHARS
        results.append(CheckItem(
            f"Jira: {title}",
            has_content,
            f"{len(content)} chars" if has_content else "missing or empty",
        ))

    review_key = _find_review_in_linked(mcp, issue_key)
    results.append(CheckItem(
        "[PLAN REVIEW] task exists",
        bool(review_key),
        review_key or "not found",
    ))

    if review_key:
        has_blocking = False
        try:
            links = mcp.jira_get_issue_links(issue_key)
            has_blocking = any(
                link["key"] == review_key and "block" in link.get("link_type", "").lower()
                for link in links
            )
        except Exception:
            pass
        results.append(CheckItem(
            "Blocking link exists",
            has_blocking,
            f"{issue_key} blocked by {review_key}" if has_blocking else "missing",
        ))

    _AI_TODO_STATUSES = ("ai to do", "ai-to-do", "ai todo")
    if status in _AI_TODO_STATUSES:
        return results

    # ── Human Plan Review: PLAN REVIEW is Done ───────────────────────────────
    review_done = False
    if review_key:
        try:
            review_text = mcp.jira_get_issue(review_key)
            review_done = "status: done" in review_text.lower()
        except Exception:
            pass
    results.append(CheckItem(
        "[PLAN REVIEW] is Done",
        review_done,
        f"{review_key} is Done" if review_done else f"{review_key or 'N/A'} not Done",
    ))

    if status in ("human plan review", "human_plan_review"):
        return results

    # ── Ready for Dev: child stories with links ──────────────────────────────
    all_linked = _get_linked_keys(mcp, issue_key)
    child_stories: list[str] = []
    for key in all_linked:
        if key == review_key:
            continue
        try:
            linked_text = mcp.jira_get_issue(key)
            first_line = linked_text.split("\n", 1)[0] if linked_text else ""
            if LAYER_RE.search(first_line):
                child_stories.append(key)
        except Exception:
            continue

    results.append(CheckItem(
        "Child stories linked",
        bool(child_stories),
        f"{len(child_stories)} found: {', '.join(child_stories)}"
        if child_stories else "none linked",
    ))

    return results


# ── Dispatcher ────────────────────────────────────────────────────────────────

def _render_checklist_table(console, items: list[CheckItem]) -> bool:
    """Render a Rich table of check items. Returns True if all checks passed."""
    from rich.table import Table

    table = Table(show_header=True, header_style="bold")
    table.add_column("Check", style="bold", min_width=28)
    table.add_column("Status", justify="center", min_width=10)
    table.add_column("Details")

    all_ok = True
    for item in items:
        if item.passed and item.fixed:
            status_str = "[cyan]✓ fixed[/cyan]"
        elif item.passed:
            status_str = "[green]✓[/green]"
        else:
            status_str = "[red]✗[/red]"
            all_ok = False

        details = item.details
        if item.fix_error:
            details += f"  [red](fix failed: {item.fix_error})[/red]"

        table.add_row(item.name, status_str, details)

    console.print(table)
    return all_ok


def run_status_check(
    mcp: MCPClientManager,
    issue_key: str,
    issue_status: str,
    config: dict,
    console,
    llm_client=None,
    output_dir: str = "outputs",
    dry_run: bool = False,
) -> int:
    """
    Run status-specific checklist validation + auto-fix.

    Called by execute.py when issue status is downstream of AI-TO-DO.

    When the issue is in 'Human Plan Review' and the PLAN REVIEW task is Done,
    promotes the issue to 'Ready for Dev' by creating child stories and
    transitioning the status.

    Returns:
        0 — all checks passed or fixed
        1 — one or more checks failed and could not be auto-fixed
    """
    if not validate_issue_key(issue_key):
        raise ValueError(f"Invalid issue key format: {issue_key}")

    project_key = extract_project_key(issue_key)
    status_lower = issue_status.lower().strip()

    console.print(f"\n[bold]Status Checklist: {issue_status}[/bold]")
    console.print(f"  Issue: {issue_key}\n")

    _AI_TODO_STATUSES = ("ai to do", "ai-to-do", "ai todo")
    _PROMOTION_STATUSES = (
        "human plan review", "human_plan_review",
        "ready for dev", "ready_for_dev",
    )

    if status_lower in _AI_TODO_STATUSES or status_lower in _PROMOTION_STATUSES:
        # Check if PLAN REVIEW task is Done → promotion flow vs. validation flow
        from .story_creator import check_review_approved

        is_done, review_key = check_review_approved(mcp, issue_key)
        already_ready = status_lower in ("ready for dev", "ready_for_dev")

        if is_done or already_ready:
            if review_key:
                console.print(
                    f"  [cyan]→[/cyan] PLAN REVIEW task {review_key} is Done "
                    "— running promotion checklist\n"
                )
            else:
                console.print(
                    "  [cyan]→[/cyan] Running promotion checklist\n"
                )
            items, created_stories = check_ready_for_dev_promotion(
                mcp, issue_key, project_key, config, dry_run=dry_run,
            )

            all_ok = _render_checklist_table(console, items)

            if all_ok and not dry_run and not already_ready:
                # Transition only when not already in Ready for Dev
                ready_status = (
                    config.get("jira", {})
                    .get("statuses", {})
                    .get("ready_for_dev", "Ready for Dev")
                )
                try:
                    mcp.jira_transition_issue(issue_key, ready_status)
                    console.print(
                        f"\n[bold green]✓ All checks passed — transitioned "
                        f"{issue_key} to '{ready_status}'[/bold green]"
                    )
                except Exception as exc:
                    console.print(
                        f"\n[bold red]✗ Checklist passed but transition "
                        f"failed: {exc}[/bold red]"
                    )
                    return 1
            elif all_ok:
                msg = "(already in Ready for Dev)" if already_ready else "(dry-run)"
                console.print(
                    f"\n[bold green]✓ All checks passed {msg}[/bold green]"
                )
            else:
                console.print(
                    "\n[bold yellow]⚠ Promotion blocked "
                    "— see failed checks above[/bold yellow]"
                )
                return 1

            return 0
        else:
            # REVIEW not Done → existing validation checklist
            items = check_human_plan_review(
                mcp, issue_key, project_key, config,
                known_review_key=review_key,
            )
    else:
        console.print(
            f"  [dim]No automated checklist defined for '{issue_status}'. "
            f"Full pipeline skipped — manual review required.[/dim]"
        )
        return 0

    all_ok = _render_checklist_table(console, items)

    if all_ok:
        console.print("\n[bold green]✓ All checks passed[/bold green]")
        return 0
    else:
        console.print(
            "\n[bold yellow]⚠ Some checks failed — see table above[/bold yellow]"
        )
        return 1
