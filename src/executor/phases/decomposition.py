"""
Analysis & Decomposition Phase.

Handles the post-LLM-execution step that:
1. Creates blocking review Story linked to Feature
2. Adds Technical Decomposition comment with Stories list
3. Adds Executor Rationale (Chain of Thoughts) comment
4. Adds Clarification Questions comment (if any)

Based on SDLC rules: Phase 1, Stage 2 "Analysis & Decomposition"
"""

import re
import logging
from typing import Optional

from ..mcp.client import MCPClientManager
from ..models.execution_context import ExecutionContext
from ..models.decomposition import (
    DecomposedStory,
    ClarificationQuestion,
    DecompositionResult,
)
from ..utils.markdown_formatter import (
    format_cot_panel,
    format_draft_comment_header,
    format_story_list,
)
from .llm_executor import LLMResponse

logger = logging.getLogger(__name__)

# Valid layers from SDLC taxonomy
VALID_LAYERS = {"BE", "FE", "INFRA", "DB", "QA", "DOCS", "GEN"}


def extract_stories(work_plan: str) -> list[DecomposedStory]:
    """
    Extract stories from LLM work plan.

    Parses format:
    - [ ] **Step N:** [description]
      - **Layer:** [BE/FE/...]
      - **Files:** [file list]
      - **Acceptance:** [criteria]

    Args:
        work_plan: Raw work plan text from LLMResponse

    Returns:
        List of DecomposedStory objects
    """
    stories = []

    if not work_plan:
        return stories

    # Pattern to match step blocks
    # Matches: - [ ] **Step N:** description followed by metadata
    step_pattern = re.compile(
        r"-\s*\[\s*\]\s*\*\*Step\s+(\d+):\*\*\s*(.+?)(?=(?:-\s*\[\s*\]\s*\*\*Step|\Z))",
        re.DOTALL | re.IGNORECASE
    )

    for match in step_pattern.finditer(work_plan):
        step_num = int(match.group(1))
        step_content = match.group(2).strip()

        # Extract layer
        layer_match = re.search(r"\*\*Layer:\*\*\s*\[?(\w+)\]?", step_content, re.IGNORECASE)
        layer = layer_match.group(1).upper() if layer_match else "GEN"
        if layer not in VALID_LAYERS:
            layer = "GEN"

        # Extract files - handle both inline and bullet formats
        files_match = re.search(r"\*\*Files:\*\*\s*(.+?)(?=-\s*\*\*|\n\n|\Z)", step_content, re.DOTALL | re.IGNORECASE)
        files_str = files_match.group(1).strip() if files_match else ""
        # Clean up and split on comma or newline, filter out bullet points
        files = [f.strip().lstrip("-").strip() for f in re.split(r"[,\n]", files_str)
                 if f.strip() and f.strip() not in ["-", ""] and not f.strip().startswith("**")]

        # Extract acceptance criteria
        acceptance_match = re.search(r"\*\*Acceptance:\*\*\s*(.+?)(?=\*\*|$)", step_content, re.DOTALL | re.IGNORECASE)
        acceptance = acceptance_match.group(1).strip() if acceptance_match else ""

        # Extract title (first line after Step N:)
        title_match = re.match(r"([^\n]+)", step_content)
        title = title_match.group(1).strip() if title_match else f"Step {step_num}"
        # Clean up title - remove metadata if present on same line
        title = re.sub(r"\s*-\s*\*\*Layer.*$", "", title, flags=re.IGNORECASE).strip()

        # Description is the cleaned content
        description = re.sub(r"-\s*\*\*(?:Layer|Files|Acceptance):\*\*.*?(?=(?:-\s*\*\*|\Z))", "", step_content, flags=re.DOTALL | re.IGNORECASE).strip()

        story = DecomposedStory(
            layer=layer,
            title=title,
            description=description,
            acceptance=acceptance,
            files=files,
            order=step_num,
        )
        stories.append(story)

    # Sort by order
    stories.sort(key=lambda s: s.order)

    return stories


def extract_questions(concerns: str) -> list[ClarificationQuestion]:
    """
    Extract clarification questions from concerns section.

    Looks for:
    - Bullet points with question marks
    - Items marked as [DATA MISSING: ...]

    Args:
        concerns: Raw concerns text from LLMResponse

    Returns:
        List of ClarificationQuestion objects (empty if no questions)
    """
    questions = []

    if not concerns:
        return questions

    # Extract [DATA MISSING: ...] markers
    missing_pattern = re.compile(r"\[DATA MISSING:\s*([^\]]+)\]", re.IGNORECASE)
    for match in missing_pattern.finditer(concerns):
        questions.append(ClarificationQuestion(
            question=f"What is {match.group(1).strip()}?",
            context=f"Data marked as missing: {match.group(1).strip()}",
            related_story=None,
        ))

    # Extract bullet points with question marks
    question_pattern = re.compile(r"[-*]\s*(.+\?)", re.MULTILINE)
    for match in question_pattern.finditer(concerns):
        question_text = match.group(1).strip()
        # Avoid duplicates from DATA MISSING
        if not any(q.question == question_text for q in questions):
            questions.append(ClarificationQuestion(
                question=question_text,
                context="From concerns section",
                related_story=None,
            ))

    return questions


def extract_complexity(analysis: str) -> str:
    """
    Extract complexity estimate from analysis section.

    Looks for: S, M, L, XL markers

    Args:
        analysis: Raw analysis text from LLMResponse

    Returns:
        Complexity string (default "M")
    """
    if not analysis:
        return "M"

    # Look for complexity markers
    complexity_pattern = re.compile(r"complexity[:\s]*[`]?([SMLX]{1,2})[`]?", re.IGNORECASE)
    match = complexity_pattern.search(analysis)
    if match:
        return match.group(1).upper()

    # Alternative: look for (S), (M), (L), (XL) patterns
    alt_pattern = re.compile(r"\(([SMLX]{1,2})\)", re.IGNORECASE)
    match = alt_pattern.search(analysis)
    if match:
        return match.group(1).upper()

    return "M"


def extract_alternatives(analysis: str) -> str:
    """
    Extract alternatives considered from analysis section.

    Looks for sections mentioning alternatives, options, or discarded approaches.

    Args:
        analysis: Raw analysis text from LLMResponse

    Returns:
        Alternatives text (empty string if none found)
    """
    if not analysis:
        return ""

    # Look for alternative mentions
    patterns = [
        r"alternative[s]?[:\s]*(.+?)(?=\n\n|\Z)",
        r"option[s]?\s+considered[:\s]*(.+?)(?=\n\n|\Z)",
        r"(?:other|discarded)\s+approach[es]*[:\s]*(.+?)(?=\n\n|\Z)",
    ]

    for pattern in patterns:
        match = re.search(pattern, analysis, re.IGNORECASE | re.DOTALL)
        if match:
            return match.group(1).strip()

    return ""


def parse_llm_response(response: LLMResponse, issue_key: str, feature_title: str) -> DecompositionResult:
    """
    Parse LLM response into DecompositionResult.

    Args:
        response: LLMResponse from Stage 5
        issue_key: Jira issue key
        feature_title: Feature summary

    Returns:
        DecompositionResult with parsed data
    """
    # Extract stories from work plan
    stories = extract_stories(response.work_plan)

    # Extract questions from concerns (optional)
    questions = extract_questions(response.concerns)

    # Extract complexity
    complexity = extract_complexity(response.analysis)

    # Extract alternatives for CoT
    alternatives = extract_alternatives(response.analysis)

    return DecompositionResult(
        stories=stories,
        questions=questions,
        cot_context=response.understanding,
        cot_decision=response.analysis,
        cot_alternatives=alternatives,
        complexity=complexity,
        feature_title=feature_title,
        review_task_key=None,
    )


def create_blocking_review_task(
    mcp: MCPClientManager,
    project_key: str,
    parent_key: str,
    issue_key: str,
    config: dict,
) -> Optional[str]:
    """
    Create blocking review Story.

    Creates: [REVIEW] {Issue_Key} Approve Architecture (HUMAN)
    Links:
    - "is child of" link to parent Feature (Classic Jira)
    - "Blocks" link to the original issue

    Args:
        mcp: MCP client manager
        project_key: Jira project key
        parent_key: Parent Feature key (for child relationship via link)
        issue_key: Original issue key (to block)
        config: SDLC config dict

    Returns:
        Created issue key, or None on failure
    """
    # Get link type from config (default for Classic Jira hierarchy)
    jira_config = config.get("jira", {})
    parent_link_type = jira_config.get("parent_link_type", "Parent")

    # Format title with issue key
    summary = f"[REVIEW] {issue_key} Approve Architecture (HUMAN)"

    description = f"""Human review required before proceeding to development.

This blocking Story must be marked as **Done** before the feature can progress.

**Parent Feature:** {parent_key}
**Blocked Issue:** {issue_key}

## Review Checklist

- [ ] Technical approach is valid
- [ ] Architecture aligns with existing patterns
- [ ] Risks have been identified and mitigated
- [ ] Stories are properly decomposed
"""

    try:
        # Create the review Story (without parent_key - Classic Jira doesn't support it for Stories)
        result = mcp.jira_create_issue(
            project_key=project_key,
            issue_type="Story",
            summary=summary,
            description=description,
        )

        # Parse created issue key from result
        review_key = None
        if isinstance(result, str):
            key_match = re.search(r"([A-Z]+-\d+)", result)
            if key_match:
                review_key = key_match.group(1)

        if not review_key:
            logger.warning(f"Could not parse issue key from create result: {result}")
            return None

        logger.info(f"Created review Story: {review_key}")

        # Link review Story as child of parent Feature (Classic Jira hierarchy)
        try:
            mcp.jira_link_issues(
                from_key=parent_key,
                to_key=review_key,
                link_type=parent_link_type,
            )
            logger.info(f"Linked {review_key} as child of {parent_key} (via '{parent_link_type}')")
        except Exception as e:
            logger.warning(f"Failed to create parent link: {e}")

        # Link review Story to block the original issue
        try:
            mcp.jira_link_issues(
                from_key=review_key,
                to_key=issue_key,
                link_type="Blocks",
            )
            logger.info(f"Created blocking link: {review_key} blocks {issue_key}")
        except Exception as e:
            logger.warning(f"Failed to create blocking link: {e}")

        return review_key

    except Exception as e:
        logger.error(f"Failed to create review task: {e}")
        return None


def build_decomposition_comment(result: DecompositionResult, parent_key: str, config: dict) -> str:
    """
    Build Technical Decomposition comment.

    Args:
        result: DecompositionResult
        parent_key: Parent Feature key
        config: SDLC config

    Returns:
        Markdown-formatted comment
    """
    lines = [
        "## Technical Decomposition",
        "",
        f"**Feature:** {parent_key} - {result.feature_title}",
        f"**Complexity:** {result.complexity}",
        "",
        "---",
        "",
        "### Proposed Stories",
        "",
        "> **Note:** Stories documented for review. Will be created as Jira issues after architecture approval.",
        "",
    ]

    if result.has_stories():
        # Create table
        lines.append("| # | Layer | Story Title | Specification |")
        lines.append("|---|-------|-------------|---------------|")

        for story in result.stories:
            # Truncate description for table
            spec = story.description[:80] + "..." if len(story.description) > 80 else story.description
            spec = spec.replace("\n", " ").replace("|", "\\|")
            lines.append(f"| {story.order} | [{story.layer}] | {story.title} | {spec} |")

        lines.append("")
        lines.append("### Story Details")
        lines.append("")

        # Detailed specs per story
        for story in result.stories:
            lines.append(f"#### [{story.layer}] {story.title}")
            lines.append("")
            if story.files:
                lines.append("**Files to modify:**")
                for f in story.files:
                    lines.append(f"- `{f}`")
                lines.append("")
            if story.acceptance:
                lines.append("**Acceptance Criteria:**")
                lines.append(story.acceptance)
                lines.append("")
    else:
        lines.append("*No stories extracted - manual planning required.*")
        lines.append("")

    lines.append("---")
    lines.append("*Generated by AI Executor. Pending human review.*")

    return "\n".join(lines)


def build_cot_comment(result: DecompositionResult, config: dict) -> str:
    """
    Build Chain of Thoughts comment using format_cot_panel().

    Args:
        result: DecompositionResult with CoT fields
        config: SDLC config

    Returns:
        Formatted CoT panel
    """
    # Build context from understanding
    context = result.cot_context if result.cot_context else "Task context not available"
    # Truncate if too long
    if len(context) > 500:
        context = context[:500] + "..."

    # Build decision from analysis
    decision = result.cot_decision if result.cot_decision else "Technical approach not specified"
    if len(decision) > 1000:
        decision = decision[:1000] + "..."

    # Alternatives (may be empty)
    alternatives = result.cot_alternatives

    return format_cot_panel(
        context=context,
        decision=decision,
        alternatives=alternatives,
    )


def build_clarifications_comment(
    result: DecompositionResult,
    issue_key: str,
    slug: str,
    config: dict,
) -> Optional[str]:
    """
    Build Clarification Questions comment.

    Only returns content if there are questions.

    Args:
        result: DecompositionResult with questions
        issue_key: Jira issue key
        slug: Project slug for header
        config: SDLC config

    Returns:
        Formatted comment, or None if no questions
    """
    if not result.has_questions():
        return None

    # Use format_draft_comment_header for the header
    header = f"### need clarification: {issue_key}_{slug}_story.md"

    lines = [
        header,
        "",
        "The following questions require human input before proceeding:",
        "",
    ]

    # Group questions by related story
    general_questions = []
    story_questions: dict[str, list[ClarificationQuestion]] = {}

    for q in result.questions:
        if q.related_story:
            if q.related_story not in story_questions:
                story_questions[q.related_story] = []
            story_questions[q.related_story].append(q)
        else:
            general_questions.append(q)

    # Add general questions
    if general_questions:
        lines.append("#### General")
        lines.append("")
        for i, q in enumerate(general_questions, 1):
            lines.append(f"{i}. **{q.question}**")
            if q.context and q.context != "From concerns section":
                lines.append(f"   - Context: {q.context}")
        lines.append("")

    # Add story-specific questions
    for story_title, questions in story_questions.items():
        lines.append(f"#### Related to {story_title}")
        lines.append("")
        for i, q in enumerate(questions, 1):
            lines.append(f"{i}. **{q.question}**")
        lines.append("")

    lines.append("---")
    lines.append("*Please respond in comments or update the Feature description.*")

    return "\n".join(lines)


def handle_analysis_decomposition(
    mcp: MCPClientManager,
    issue_key: str,
    execution_context: ExecutionContext,
    llm_response: LLMResponse,
    config: dict,
) -> DecompositionResult:
    """
    Handle the Analysis & Decomposition stage.

    Creates Jira artifacts:
    1. Blocking review Story (child of Feature, blocks original issue)
    2. Technical Decomposition comment
    3. Executor Rationale (CoT) comment
    4. Clarification Questions comment (optional)

    Args:
        mcp: MCP client manager
        issue_key: Jira issue key being processed
        execution_context: ExecutionContext from pipeline
        llm_response: LLMResponse from Stage 5
        config: SDLC config dict

    Returns:
        DecompositionResult with created artifact references
    """
    logger.info(f"Analysis & Decomposition for {issue_key}")

    # Parse LLM response
    result = parse_llm_response(
        response=llm_response,
        issue_key=issue_key,
        feature_title=execution_context.jira.summary,
    )

    logger.info(f"Parsed {len(result.stories)} stories, {len(result.questions)} questions")

    # Get parent key (Feature key for Story hierarchy)
    # The issue being processed is usually a Feature, so parent_key is the issue_key itself
    # But if it's a Story, we need the parent Feature key
    parent_key = execution_context.jira.parent_key or issue_key
    project_key = execution_context.jira.project_key

    # Generate slug from feature title
    slug = re.sub(r"[^a-z0-9]+", "_", execution_context.jira.summary.lower())[:30].strip("_")

    # 1. Create blocking review task
    review_task_key = create_blocking_review_task(
        mcp=mcp,
        project_key=project_key,
        parent_key=parent_key,
        issue_key=issue_key,
        config=config,
    )
    result.review_task_key = review_task_key

    if review_task_key:
        logger.info(f"Created review task: {review_task_key}")
    else:
        logger.warning("Failed to create review task")

    # 2. Add Technical Decomposition comment
    decomposition_comment = build_decomposition_comment(result, parent_key, config)
    try:
        mcp.jira_add_comment(issue_key, decomposition_comment)
        logger.info(f"Added decomposition comment to {issue_key}")
    except Exception as e:
        logger.error(f"Failed to add decomposition comment: {e}")

    # 3. Add Executor Rationale (CoT) comment
    cot_comment = build_cot_comment(result, config)
    try:
        mcp.jira_add_comment(issue_key, cot_comment)
        logger.info(f"Added CoT comment to {issue_key}")
    except Exception as e:
        logger.error(f"Failed to add CoT comment: {e}")

    # 4. Add Clarification Questions comment (if any)
    clarifications_comment = build_clarifications_comment(result, issue_key, slug, config)
    if clarifications_comment:
        try:
            mcp.jira_add_comment(issue_key, clarifications_comment)
            logger.info(f"Added clarifications comment to {issue_key}")
        except Exception as e:
            logger.error(f"Failed to add clarifications comment: {e}")

    logger.info(f"Analysis & Decomposition complete for {issue_key}")
    return result
