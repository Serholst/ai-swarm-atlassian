"""
Post-Execution Handler - Automated Jira transitions based on pipeline results.

This module handles the automated workflow transitions after pipeline execution:
- On SUCCESS: AI-TO-DO → Human Plan Review (with plan summary comment)
  - Also executes Analysis & Decomposition (creates review Story, adds comments)
- On FAILURE: AI-TO-DO → Backlog (with error explanation comment)

Replaces the manual "Analysis" column in Jira workflow.
"""

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, TYPE_CHECKING

from ..mcp.client import MCPClientManager
from ..models.execution_context import (
    ExecutionContext,
    ProjectStatus,
    ContextLocationError,
)
from ..models.decomposition import DecompositionResult

if TYPE_CHECKING:
    from .llm_executor import LLMResponse

logger = logging.getLogger(__name__)


class ExecutionOutcome(str, Enum):
    """Outcome of pipeline execution."""
    SUCCESS = "success"
    CONTEXT_ERROR = "context_error"
    NEW_PROJECT = "new_project"
    EXECUTION_ERROR = "execution_error"


@dataclass
class TransitionResult:
    """Result of post-execution transition."""
    outcome: ExecutionOutcome
    target_status: str
    comment_added: bool
    error: Optional[str] = None
    decomposition_result: Optional[DecompositionResult] = None


# Jira status names (must match workflow exactly)
STATUS_BACKLOG = "Backlog"
STATUS_HUMAN_PLAN_REVIEW = "Human Plan Review"


def determine_outcome(
    execution_context: Optional[ExecutionContext],
    execution_error: Optional[Exception] = None,
) -> tuple[ExecutionOutcome, list[str]]:
    """
    Determine execution outcome based on context and errors.

    Args:
        execution_context: The built execution context (may be None on error)
        execution_error: Any exception that occurred during execution

    Returns:
        (outcome, list of issues/missing items)
    """
    issues = []

    # Check for execution error first
    if execution_error:
        if isinstance(execution_error, ContextLocationError):
            issues.append(str(execution_error))
            return ExecutionOutcome.CONTEXT_ERROR, issues
        else:
            issues.append(f"Execution error: {execution_error}")
            return ExecutionOutcome.EXECUTION_ERROR, issues

    if not execution_context:
        issues.append("No execution context available")
        return ExecutionOutcome.EXECUTION_ERROR, issues

    # Check refined Confluence context for project status
    if execution_context.refined_confluence:
        rc = execution_context.refined_confluence

        # BRAND_NEW status (no project_link AND no project_folder) - proceed to SUCCESS
        # The LLM will be instructed to include page creation steps in the plan
        if rc.project_status == ProjectStatus.BRAND_NEW:
            issues.append("Brand new project - Confluence pages need to be created")
            # Fall through to SUCCESS check (don't return early)

        # NEW_PROJECT status means folder exists but mandatory docs are missing
        # Treat the same as BRAND_NEW: proceed to LLM with missing data as context
        elif rc.project_status == ProjectStatus.NEW_PROJECT:
            issues.extend(rc.missing_critical_data)
            # Fall through to SUCCESS check (don't return early)

        # INCOMPLETE status means pages exist but are empty or lack content
        # Proceed to LLM which will generate steps to fill them
        elif rc.project_status == ProjectStatus.INCOMPLETE:
            issues.extend(rc.missing_critical_data)
            # Fall through to SUCCESS check (don't return early)

        # Check for missing critical data even if not NEW_PROJECT/BRAND_NEW/INCOMPLETE
        elif rc.missing_critical_data:
            issues.extend(rc.missing_critical_data)

    # Check for context validation errors
    if execution_context.errors:
        issues.extend(execution_context.errors)

    # If we have issues but context is technically valid, still success
    # The issues will be noted in the comment
    if execution_context.is_valid():
        return ExecutionOutcome.SUCCESS, issues

    return ExecutionOutcome.CONTEXT_ERROR, issues


def build_failure_comment(
    issue_key: str,
    outcome: ExecutionOutcome,
    issues: list[str],
    jira_summary: Optional[str] = None,
) -> str:
    """
    Build comment for failed execution / insufficient context.

    Args:
        issue_key: The Jira issue key
        outcome: The execution outcome
        issues: List of issues/missing items
        jira_summary: Optional task summary

    Returns:
        Markdown-formatted comment
    """
    lines = ["## AI Executor - Context Insufficient", ""]

    if jira_summary:
        lines.append(f"**Task:** {jira_summary}")
        lines.append("")

    # Explain the outcome
    if outcome == ExecutionOutcome.NEW_PROJECT:
        lines.append("### New Project Detected")
        lines.append("")
        lines.append("This task references a project that doesn't have the required documentation in Confluence.")
        lines.append("")
        lines.append("**Missing mandatory documents:**")
    elif outcome == ExecutionOutcome.CONTEXT_ERROR:
        lines.append("### Context Location Error")
        lines.append("")
        lines.append("The system could not locate the required project context.")
        lines.append("")
        lines.append("**Issues:**")
    else:
        lines.append("### Execution Error")
        lines.append("")
        lines.append("An error occurred during pipeline execution.")
        lines.append("")
        lines.append("**Errors:**")

    for issue in issues:
        lines.append(f"- {issue}")

    lines.append("")
    lines.append("### Required Actions")
    lines.append("")

    if outcome == ExecutionOutcome.NEW_PROJECT:
        lines.append("1. Create **Project Passport** page in Confluence under the project folder")
        lines.append("2. Create **Logical Architecture** page with system design")
        lines.append("3. Ensure the Jira issue has the correct **Project** field set")
        lines.append("4. Move this task back to AI-TO-DO when ready")
    elif outcome == ExecutionOutcome.CONTEXT_ERROR:
        lines.append("1. Verify the **Project** custom field in Jira is set correctly")
        lines.append("2. Ensure the project folder exists in Confluence")
        lines.append("3. Check that the Confluence space key matches the Jira project key")
        lines.append("4. Move this task back to AI-TO-DO when ready")
    else:
        lines.append("1. Review the error details above")
        lines.append("2. Fix any configuration or access issues")
        lines.append("3. Move this task back to AI-TO-DO when ready")

    lines.append("")
    lines.append("---")
    lines.append("*Task returned to Backlog for refinement.*")

    return "\n".join(lines)


def handle_post_execution(
    mcp: MCPClientManager,
    issue_key: str,
    execution_context: Optional[ExecutionContext] = None,
    execution_error: Optional[Exception] = None,
    plan_summary: Optional[str] = None,
    llm_response: Optional["LLMResponse"] = None,
    config: Optional[dict] = None,
    dry_run: bool = False,
) -> TransitionResult:
    """
    Handle post-execution Jira transition.

    This replaces the manual Analysis column by automatically:
    - Moving successful executions to Human Plan Review
    - Moving failed/insufficient context to Backlog

    On SUCCESS: executes Analysis & Decomposition and posts a single
    consolidated ADF comment with expand blocks (context, decomposition,
    rationale, clarifications).

    On FAILURE: posts a Markdown failure comment.

    Args:
        mcp: MCP client manager
        issue_key: Jira issue key
        execution_context: The built execution context (may be None)
        execution_error: Any exception from pipeline execution
        plan_summary: Summary of generated plan (for success comment)
        llm_response: LLMResponse from Stage 5 (for decomposition)
        config: SDLC config dict (for decomposition)
        dry_run: If True, don't actually transition (just log)

    Returns:
        TransitionResult with outcome details
    """
    logger.info(f"Post-execution handler for {issue_key}")

    # Determine outcome
    outcome, issues = determine_outcome(execution_context, execution_error)
    logger.info(f"Outcome: {outcome.value}, issues: {issues}")

    decomposition_result: Optional[DecompositionResult] = None

    if outcome == ExecutionOutcome.SUCCESS:
        target_status = STATUS_HUMAN_PLAN_REVIEW
    else:
        target_status = STATUS_BACKLOG

    logger.info(f"Target status: {target_status}")

    if dry_run:
        logger.info(f"[DRY-RUN] Would transition {issue_key} to '{target_status}'")
        return TransitionResult(
            outcome=outcome,
            target_status=target_status,
            comment_added=False,
        )

    try:
        if outcome == ExecutionOutcome.SUCCESS and llm_response and execution_context and config:
            # Execute Analysis & Decomposition (creates review task, parses response)
            logger.info(f"Executing Analysis & Decomposition for {issue_key}")
            try:
                from .decomposition import (
                    handle_analysis_decomposition,
                    build_consolidated_adf_comment,
                )

                decomposition_result = handle_analysis_decomposition(
                    mcp=mcp,
                    issue_key=issue_key,
                    execution_context=execution_context,
                    llm_response=llm_response,
                    config=config,
                )
                logger.info(
                    f"Decomposition complete: {len(decomposition_result.stories)} stories, "
                    f"review_task={decomposition_result.review_task_key}"
                )

                # Build and post single consolidated ADF comment
                parent_key = execution_context.jira.parent_key or issue_key
                adf_comment = build_consolidated_adf_comment(
                    result=decomposition_result,
                    parent_key=parent_key,
                    execution_context=execution_context,
                    plan_summary=plan_summary,
                    issues=issues if issues else None,
                )
                mcp.jira_add_comment_adf(issue_key, adf_comment)
                logger.info(f"Added consolidated ADF comment to {issue_key}")

            except Exception as decomp_error:
                logger.error(
                    f"Decomposition/comment failed (continuing with transition): {decomp_error}"
                )
        else:
            # Non-SUCCESS: post failure comment as Markdown
            jira_summary = execution_context.jira.summary if execution_context else None
            comment = build_failure_comment(issue_key, outcome, issues, jira_summary)
            mcp.jira_add_comment(issue_key, comment)
            logger.info(f"Added failure comment to {issue_key}")

        # Transition to target status
        mcp.jira_transition_issue(issue_key, target_status)
        logger.info(f"Transitioned {issue_key} to '{target_status}'")

        return TransitionResult(
            outcome=outcome,
            target_status=target_status,
            comment_added=True,
            decomposition_result=decomposition_result,
        )

    except Exception as e:
        logger.error(f"Post-execution handler error: {e}")
        return TransitionResult(
            outcome=outcome,
            target_status=target_status,
            comment_added=False,
            error=str(e),
            decomposition_result=decomposition_result,
        )
