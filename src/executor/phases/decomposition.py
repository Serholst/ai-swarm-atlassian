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
from ..mcp.servers.jira_server import MarkdownToADF
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
        if not layer_match:
            logger.warning(f"Step {step_num}: No Layer tag found, defaulting to GEN")
            layer = "GEN"
        else:
            layer = layer_match.group(1).upper()
            if layer not in VALID_LAYERS:
                logger.warning(f"Step {step_num}: Invalid layer '{layer}', defaulting to GEN")
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

        # Extract dependencies
        depends_match = re.search(
            r"\*\*Depends on:\*\*\s*(.+?)(?=-\s*\*\*|\n\n|\Z)", step_content, re.DOTALL | re.IGNORECASE
        )
        depends_on: list[int] = []
        if depends_match:
            deps_text = depends_match.group(1).strip()
            if deps_text.lower() not in ("none", "n/a", "-", ""):
                dep_nums = re.findall(r"Step\s+(\d+)", deps_text, re.IGNORECASE)
                depends_on = [int(n) for n in dep_nums]

        # Extract title (first line after Step N:)
        title_match = re.match(r"([^\n]+)", step_content)
        title = title_match.group(1).strip() if title_match else f"Step {step_num}"
        # Clean up title - remove metadata if present on same line
        title = re.sub(r"\s*-\s*\*\*Layer.*$", "", title, flags=re.IGNORECASE).strip()

        # Description is the cleaned content
        description = re.sub(r"-\s*\*\*(?:Layer|Files|Acceptance|Depends on):\*\*.*?(?=(?:-\s*\*\*|\Z))", "", step_content, flags=re.DOTALL | re.IGNORECASE).strip()

        story = DecomposedStory(
            layer=layer,
            title=title,
            description=description,
            acceptance=acceptance,
            files=files,
            order=step_num,
            depends_on=depends_on,
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


def parse_llm_response(
    response: LLMResponse,
    issue_key: str,
    feature_title: str,
    execution_context: Optional[ExecutionContext] = None,
) -> DecompositionResult:
    """
    Parse LLM response into DecompositionResult.

    Args:
        response: LLMResponse from Stage 5
        issue_key: Jira issue key
        feature_title: Feature summary
        execution_context: Optional context for confidence scoring

    Returns:
        DecompositionResult with parsed data and confidence scores
    """
    # Extract stories from work plan
    stories = extract_stories(response.work_plan)

    # Score confidence for each story
    from .confidence import score_story_confidence, score_overall_confidence, flag_low_confidence

    for story in stories:
        score, flags = score_story_confidence(story, execution_context)
        story.confidence = score
        story.confidence_flags = flags

    overall_confidence = score_overall_confidence(stories)
    low_confidence = flag_low_confidence(stories)

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
        overall_confidence=overall_confidence,
        low_confidence_stories=low_confidence,
        review_task_key=None,
    )


def create_blocking_review_task(
    mcp: MCPClientManager,
    project_key: str,
    issue_key: str,
) -> Optional[str]:
    """
    Create blocking review Story.

    Creates: [REVIEW] {Issue_Key} Approve Architecture (HUMAN)
    Links: "Blocks" link to the original issue

    Args:
        mcp: MCP client manager
        project_key: Jira project key
        issue_key: Original issue key (to block)

    Returns:
        Created issue key, or None on failure
    """
    summary = f"[REVIEW] {issue_key} Approve Architecture (HUMAN)"

    description = f"""Human review required before proceeding to development.

This blocking Story must be marked as **Done** before the feature can progress.

**Blocked Issue:** {issue_key}

## Review Checklist

- [ ] Technical approach is valid
- [ ] Architecture aligns with existing patterns
- [ ] Risks have been identified and mitigated
- [ ] Stories are properly decomposed
"""

    try:
        result = mcp.jira_create_issue(
            project_key=project_key,
            issue_type="Story",
            summary=summary,
            description=description,
        )

        review_key = None
        if isinstance(result, str):
            key_match = re.search(r"([A-Z]+-\d+)", result)
            if key_match:
                review_key = key_match.group(1)

        if not review_key:
            logger.warning(f"Could not parse issue key from create result: {result}")
            return None

        logger.info(f"Created review Story: {review_key}")

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


def build_consolidated_adf_comment(
    result: DecompositionResult,
    parent_key: str,
    execution_context: ExecutionContext,
    plan_summary: Optional[str] = None,
    issues: Optional[list[str]] = None,
) -> dict:
    """
    Build a single consolidated ADF comment for Analysis & Decomposition.

    Produces one ADF document with expand blocks:
    1. Context Summary (task info, documents, repo)
    2. Technical Decomposition (native ADF table + story details)
    3. Executor Rationale (CoT)
    4. Clarification Questions (optional)

    Args:
        result: DecompositionResult with stories, CoT, questions
        parent_key: Parent Feature key
        execution_context: ExecutionContext for context summary
        plan_summary: Optional work plan summary
        issues: Optional non-blocking issues list

    Returns:
        ADF document dict ready for jira_add_comment_adf
    """
    from ..models.execution_context import ProjectStatus

    content: list[dict] = []

    # Title heading
    content.append(MarkdownToADF._heading("AI Executor -- Analysis & Decomposition", 2))

    # --- Expand 1: Context Summary ---
    ctx_lines = [f"**Task:** {execution_context.jira.summary}", ""]
    if execution_context.refined_confluence:
        rc = execution_context.refined_confluence
        ctx_lines.append(f"**Project Space:** {rc.project_space}")
        ctx_lines.append(f"**Project Status:** {rc.project_status.value}")
        ctx_lines.append("")

        if rc.project_status in (
            ProjectStatus.BRAND_NEW,
            ProjectStatus.NEW_PROJECT,
            ProjectStatus.INCOMPLETE,
        ):
            for item in rc.missing_critical_data or []:
                ctx_lines.append(f"- {item}")
            ctx_lines.append("")

        if rc.core_documents:
            ctx_lines.append("**Core Documents:**")
            for doc in rc.core_documents:
                ctx_lines.append(f"- [{doc.title}]({doc.url})")
            ctx_lines.append("")

        if rc.supporting_documents:
            ctx_lines.append(f"**Supporting Documents ({len(rc.supporting_documents)}):**")
            for doc in rc.supporting_documents:
                ctx_lines.append(f"- [{doc.title}]({doc.url})")
            ctx_lines.append("")

    if execution_context.github and execution_context.github.repository_url:
        ctx_lines.append(f"**Repository:** {execution_context.github.repository_url}")
        if execution_context.github.primary_language:
            ctx_lines.append(f"**Language:** {execution_context.github.primary_language}")
        ctx_lines.append("")

    if plan_summary:
        ctx_lines.append("### Work Plan Summary")
        ctx_lines.append("")
        ctx_lines.append(plan_summary[:1500])
        ctx_lines.append("")

    if issues:
        ctx_lines.append("### Notes")
        for issue in issues:
            ctx_lines.append(f"- {issue}")
        ctx_lines.append("")

    content.append(MarkdownToADF.expand_block("Context Summary", "\n".join(ctx_lines)))

    # --- Expand 2: Technical Decomposition ---
    decomp_nodes: list[dict] = []

    # Feature info header
    header_md = (
        f"**Feature:** {parent_key} - {result.feature_title}\n"
        f"**Complexity:** {result.complexity}\n"
        f"**Overall Confidence:** {result.overall_confidence:.0%}\n\n"
        "> Stories documented for review. Will be created as Jira issues "
        "after architecture approval."
    )
    decomp_nodes.extend(MarkdownToADF.convert(header_md).get("content", []))

    if result.has_stories():
        # Native ADF table
        headers = ["#", "Layer", "Story Title", "Confidence", "Specification"]
        rows = []
        for story in result.stories:
            spec = (
                story.description[:80] + "..."
                if len(story.description) > 80
                else story.description
            )
            spec = spec.replace("\n", " ")
            conf_label = "HIGH" if story.confidence >= 0.7 else "LOW"
            conf_str = f"{story.confidence:.0%} ({conf_label})"
            rows.append(
                [str(story.order), f"[{story.layer}]", story.title, conf_str, spec]
            )
        decomp_nodes.append(MarkdownToADF.table(headers, rows))

        # Low confidence warnings
        if result.low_confidence_stories:
            warn_lines = [
                "### Low Confidence Stories",
                "",
                "> The following stories may need additional review:",
                "",
            ]
            for order in result.low_confidence_stories:
                story = next((s for s in result.stories if s.order == order), None)
                if story:
                    warn_lines.append(f"- **Step {order}:** {story.title}")
                    for flag in story.confidence_flags:
                        warn_lines.append(f"  - {flag}")
            decomp_nodes.extend(
                MarkdownToADF.convert("\n".join(warn_lines)).get("content", [])
            )

        # Story details
        details_lines = ["### Story Details", ""]
        for story in result.stories:
            details_lines.append(f"#### [{story.layer}] {story.title}")
            details_lines.append(f"**Confidence:** {story.confidence:.0%}")
            details_lines.append("")
            if story.files:
                details_lines.append("**Files to modify:**")
                for f in story.files:
                    details_lines.append(f"- `{f}`")
                details_lines.append("")
            if story.acceptance:
                details_lines.append("**Acceptance Criteria:**")
                details_lines.append(story.acceptance)
                details_lines.append("")
            if story.depends_on:
                deps_str = ", ".join(f"Step {d}" for d in story.depends_on)
                details_lines.append(f"**Depends on:** {deps_str}")
                details_lines.append("")
        decomp_nodes.extend(
            MarkdownToADF.convert("\n".join(details_lines)).get("content", [])
        )
    else:
        decomp_nodes.extend(
            MarkdownToADF.convert(
                "*No stories extracted - manual planning required.*"
            ).get("content", [])
        )

    content.append({
        "type": "expand",
        "attrs": {"title": "Technical Decomposition"},
        "content": decomp_nodes,
    })

    # --- Expand 3: Executor Rationale ---
    ctx_text = result.cot_context or "Task context not available"
    if len(ctx_text) > 500:
        ctx_text = ctx_text[:500] + "..."
    decision_text = result.cot_decision or "Technical approach not specified"
    if len(decision_text) > 1000:
        decision_text = decision_text[:1000] + "..."

    cot_lines = [
        f"**Context:** {ctx_text}",
        "",
        f"**Decision:** {decision_text}",
    ]
    if result.cot_alternatives:
        cot_lines.append("")
        cot_lines.append(f"**Alternatives Discarded:** {result.cot_alternatives}")

    content.append(MarkdownToADF.expand_block("Executor Rationale", "\n".join(cot_lines)))

    # --- Expand 4: Clarification Questions (optional) ---
    if result.has_questions():
        q_lines = ["The following questions require human input:", ""]
        general_questions = [q for q in result.questions if not q.related_story]
        story_questions: dict[str, list[ClarificationQuestion]] = {}
        for q in result.questions:
            if q.related_story:
                story_questions.setdefault(q.related_story, []).append(q)

        if general_questions:
            q_lines.append("#### General")
            q_lines.append("")
            for i, q in enumerate(general_questions, 1):
                q_lines.append(f"{i}. **{q.question}**")
                if q.context and q.context != "From concerns section":
                    q_lines.append(f"   - Context: {q.context}")
            q_lines.append("")

        for story_title, questions in story_questions.items():
            q_lines.append(f"#### Related to {story_title}")
            q_lines.append("")
            for i, q in enumerate(questions, 1):
                q_lines.append(f"{i}. **{q.question}**")
            q_lines.append("")

        content.append(
            MarkdownToADF.expand_block("Clarification Questions", "\n".join(q_lines))
        )

    # Footer
    content.append(MarkdownToADF._paragraph("Generated by AI Executor. Pending human review."))

    return {"type": "doc", "version": 1, "content": content}


def handle_analysis_decomposition(
    mcp: MCPClientManager,
    issue_key: str,
    execution_context: ExecutionContext,
    llm_response: LLMResponse,
    config: dict,
) -> DecompositionResult:
    """
    Handle the Analysis & Decomposition stage.

    Creates blocking review Story and parses LLM response.
    The consolidated ADF comment is built and posted by handle_post_execution().

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

    # Parse LLM response (with confidence scoring)
    result = parse_llm_response(
        response=llm_response,
        issue_key=issue_key,
        feature_title=execution_context.jira.summary,
        execution_context=execution_context,
    )

    logger.info(f"Parsed {len(result.stories)} stories, {len(result.questions)} questions")

    project_key = execution_context.jira.project_key

    # Create blocking review task
    review_task_key = create_blocking_review_task(
        mcp=mcp,
        project_key=project_key,
        issue_key=issue_key,
    )
    result.review_task_key = review_task_key

    if review_task_key:
        logger.info(f"Created review task: {review_task_key}")
    else:
        logger.warning("Failed to create review task")

    logger.info(f"Analysis & Decomposition complete for {issue_key}")
    return result
