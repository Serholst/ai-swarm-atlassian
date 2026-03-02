"""
Story Creator — creates Jira Story issues from approved decomposition.

Called automatically by human_plan_review_handler when --task detects
[PLAN REVIEW] is Done on a "Human Plan Review" issue.

Workflow:
1. Check that [PLAN REVIEW] task is marked Done
2. Re-extract stories from Technical Decomposition comment
3. Create Jira Story issues linked to parent Feature (idempotent)
"""

import json
import re
import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Literal, Optional

from ..constants import extract_project_key, get_blocking_link_type
from ..mcp.client import MCPClientManager
from ..models.decomposition import DecomposedStory
from ..utils.file_utils import atomic_write

logger = logging.getLogger(__name__)


# =============================================================================
# Creation Report (Gap 2)
# =============================================================================


@dataclass
class StoryCreationOutcome:
    """Outcome of creating (or skipping) a single story."""

    story: DecomposedStory
    jira_key: Optional[str] = None  # None if failed
    status: Literal["created", "skipped", "failed"] = "failed"
    error: Optional[str] = None

    def to_manifest_dict(self) -> dict:
        return {
            "order": self.story.order,
            "jira_key": self.jira_key,
            "summary": f"[{self.story.layer}] {self.story.title}",
            "status": self.status,
            "error": self.error,
        }


@dataclass
class StoryCreationReport:
    """Aggregated report of all story creation outcomes."""

    outcomes: list[StoryCreationOutcome] = field(default_factory=list)

    @property
    def created(self) -> list[tuple[DecomposedStory, str]]:
        return [(o.story, o.jira_key) for o in self.outcomes if o.status == "created"]

    @property
    def skipped(self) -> list[StoryCreationOutcome]:
        return [o for o in self.outcomes if o.status == "skipped"]

    @property
    def failed(self) -> list[StoryCreationOutcome]:
        return [o for o in self.outcomes if o.status == "failed"]

    @property
    def created_or_skipped(self) -> list[tuple[DecomposedStory, str]]:
        """All stories that exist in Jira (created this run or previously)."""
        return [
            (o.story, o.jira_key)
            for o in self.outcomes
            if o.status in ("created", "skipped") and o.jira_key
        ]


# =============================================================================
# Manifest (Gap 1)
# =============================================================================


def _manifest_path(parent_key: str, output_dir: str = "outputs") -> Path:
    issue_dir = Path(output_dir) / parent_key
    issue_dir.mkdir(parents=True, exist_ok=True)
    return issue_dir / f"{parent_key}_stories_manifest.json"


def _load_manifest(parent_key: str, output_dir: str = "outputs") -> dict:
    """Load existing story creation manifest, or return empty dict."""
    path = _manifest_path(parent_key, output_dir)
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"Could not load manifest {path}: {e}")
    return {}


def _save_manifest(
    parent_key: str,
    report: StoryCreationReport,
    output_dir: str = "outputs",
) -> Path:
    """Save creation manifest (atomic write)."""
    data = {
        "parent_key": parent_key,
        "created_at": datetime.now().isoformat(),
        "stories": [o.to_manifest_dict() for o in report.outcomes],
    }
    path = _manifest_path(parent_key, output_dir)
    atomic_write(path, json.dumps(data, indent=2, ensure_ascii=False))
    logger.info(f"Saved story manifest: {path}")
    return path


def _get_existing_child_summaries(
    mcp: MCPClientManager, parent_key: str
) -> dict[str, str]:
    """
    Query Jira for existing child stories of a parent issue.

    Returns:
        Dict mapping normalized summary -> jira_key for existing children.
    """
    jql = f'parent = "{parent_key}" AND issuetype = Story'
    try:
        result = mcp.jira_search_issues(jql, max_results=100)
    except Exception as e:
        logger.warning(f"Could not query existing children for {parent_key}: {e}")
        return {}

    existing: dict[str, str] = {}
    if isinstance(result, str):
        # MCP returns formatted text — parse keys and summaries
        for line in result.splitlines():
            key_match = re.search(r"\*\*([A-Z][A-Z0-9]*-\d+)\*\*", line)
            if key_match:
                key = key_match.group(1)
                # Try to extract summary after the key
                summary_match = re.search(
                    r"\*\*[A-Z][A-Z0-9]*-\d+\*\*[:\s]*(.+?)(?:\s*\||\s*$)", line
                )
                if summary_match:
                    existing[summary_match.group(1).strip().lower()] = key
    return existing


def _is_empty_search(result: str | None) -> bool:
    """Check if a JQL search result is empty (no issues found)."""
    if not result:
        return True
    return "Found 0 issues" in result


def _parse_review_from_search(result: str, issue_key: str) -> tuple[Optional[str], bool]:
    """
    Parse search results to find a [PLAN REVIEW] task key and its Done status.

    Returns:
        (review_key, is_done) — review_key is None if not found
    """
    review_key = None
    is_done = False

    lines = result.splitlines()
    for i, line in enumerate(lines):
        key_match = re.search(r"\*\*([A-Z][A-Z0-9]*-\d+)\*\*", line)
        if key_match:
            candidate_key = key_match.group(1)
            if f"[PLAN REVIEW] {issue_key}" in line or "[PLAN REVIEW]" in line or "[REVIEW]" in line:
                search_block = line
                if i + 1 < len(lines):
                    search_block += "\n" + lines[i + 1]
                status_match = re.search(r"Status:\s*(\w+)", search_block, re.IGNORECASE)
                if status_match and status_match.group(1).lower() == "done":
                    return candidate_key, True
                elif not review_key:
                    review_key = candidate_key

    return review_key, is_done


def check_review_approved(mcp: MCPClientManager, issue_key: str) -> tuple[bool, Optional[str]]:
    """
    Check if the [PLAN REVIEW] task for this feature is Done.

    Strategy 1 (primary): Direct REST API issuelinks field — 100% reliable,
    bypasses JQL search index entirely.

    Strategy 2+ (fallback): JQL-based search if direct API fails.

    Args:
        mcp: MCP client manager
        issue_key: Feature issue key

    Returns:
        Tuple of (is_approved, review_task_key)
    """
    project_key = extract_project_key(issue_key)

    # Strategy 1: Direct REST API — read issuelinks field (most reliable)
    try:
        links = mcp.jira_get_issue_links(issue_key)
        for link in links:
            summary = link.get("summary", "")
            if "[PLAN REVIEW]" in summary or "[REVIEW]" in summary:
                is_done = link.get("status", "").lower() == "done"
                logger.info(
                    f"Found review task {link['key']} via direct issuelinks for {issue_key}"
                )
                return is_done, link["key"]
    except Exception as e:
        logger.debug(f"Direct issuelinks lookup failed for {issue_key}: {e}")

    # Strategy 2 (fallback): JQL summary search
    strategies: list[tuple[str, str]] = [
        (
            "summary",
            f'project = "{project_key}" AND '
            f'summary ~ "Approve Architecture" AND '
            f'summary ~ "{issue_key}" AND '
            f'issuetype = Story',
        ),
        (
            "blocking link",
            f'issue in linkedIssues("{issue_key}", "is blocked by") AND '
            f'summary ~ "Approve Architecture" AND '
            f'issuetype = Story',
        ),
    ]

    for label, jql in strategies:
        try:
            result = mcp.jira_search_issues(jql, max_results=5)
            if _is_empty_search(result):
                continue
            review_key, is_done = _parse_review_from_search(result, issue_key)
            if review_key:
                logger.info(f"Found review task {review_key} via {label} for {issue_key}")
                if not is_done:
                    try:
                        issue_details = mcp.jira_get_issue(review_key)
                        if "status: done" in issue_details.lower():
                            is_done = True
                    except Exception as e:
                        logger.error(f"Failed to check review task status: {e}")
                return is_done, review_key
        except Exception as e:
            logger.error(f"Failed search strategy '{label}' for {issue_key}: {e}")

    # Strategy 3: for child Stories, find parent Feature via direct API
    # and look up its review task
    try:
        links = mcp.jira_get_issue_links(issue_key)
        parent_keys = [
            link["key"] for link in links
            # No reliable way to filter by type from issuelinks alone,
            # so we'll check each linked issue
        ]
        for pk in parent_keys:
            try:
                pk_links = mcp.jira_get_issue_links(pk)
                for pk_link in pk_links:
                    summary = pk_link.get("summary", "")
                    if "[PLAN REVIEW]" in summary or "[REVIEW]" in summary:
                        is_done = pk_link.get("status", "").lower() == "done"
                        logger.info(
                            f"Found review task {pk_link['key']} via parent "
                            f"Feature {pk} for {issue_key}"
                        )
                        return is_done, pk_link["key"]
            except Exception:
                continue
    except Exception as e:
        logger.error(f"Failed parent Feature lookup for {issue_key}: {e}")

    # Fallback: search comments for explicit "review task: KEY" reference
    try:
        comments = mcp.jira_get_comments(issue_key)
        for line in comments.splitlines():
            key_match = re.search(
                r"review task:\s*([A-Z][A-Z0-9]*-\d+)", line, re.IGNORECASE
            )
            if key_match:
                review_key = key_match.group(1)
                logger.info(f"Found review task {review_key} via comments for {issue_key}")
                is_done = False
                try:
                    issue_details = mcp.jira_get_issue(review_key)
                    if "status: done" in issue_details.lower():
                        is_done = True
                except Exception:
                    pass
                return is_done, review_key
    except Exception:
        pass

    logger.warning(f"Could not find [PLAN REVIEW] task for {issue_key}")
    return False, None


def extract_stories_from_comment(mcp: MCPClientManager, issue_key: str) -> list[DecomposedStory]:
    """
    Extract stories from the Technical Decomposition comment on the feature.

    Parses the markdown table and story details sections from Jira comments.

    Args:
        mcp: MCP client manager
        issue_key: Feature issue key

    Returns:
        List of DecomposedStory objects (empty if not found)
    """
    try:
        comments = mcp.jira_get_comments(issue_key)
    except Exception as e:
        logger.error(f"Failed to get comments for {issue_key}: {e}")
        return []

    # Find the Technical Decomposition comment
    decomposition_text = None
    # Split comments by comment boundaries (author/date headers)
    # The decomposition comment starts with "## Technical Decomposition"
    comment_blocks = re.split(r"---\n\n\*\*", comments)

    for block in comment_blocks:
        if "## Technical Decomposition" in block:
            # Extract from ## Technical Decomposition to end of this comment
            start = block.index("## Technical Decomposition")
            decomposition_text = block[start:]
            break

    # Fallback: search the whole comments text
    if not decomposition_text:
        match = re.search(
            r"## Technical Decomposition.*?(?=---\n\*Generated by AI Executor)",
            comments,
            re.DOTALL,
        )
        if match:
            decomposition_text = match.group(0)

    if not decomposition_text:
        logger.warning(f"No Technical Decomposition comment found on {issue_key}")
        return []

    # Parse story details sections
    # Format: #### [LAYER] Title followed by Files and Acceptance
    stories = []
    story_pattern = re.compile(
        r"####\s*\[(\w+)\]\s*(.+?)(?=####|\Z)",
        re.DOTALL,
    )

    order = 0
    for match in story_pattern.finditer(decomposition_text):
        order += 1
        layer = match.group(1).strip()
        content = match.group(2).strip()

        # Extract title from first line
        title_line = content.split("\n")[0].strip()

        # Extract confidence
        confidence = 0.0
        conf_match = re.search(r"\*\*Confidence:\*\*\s*(\d+)%", content)
        if conf_match:
            confidence = int(conf_match.group(1)) / 100.0

        # Extract files
        files = []
        files_section = re.search(
            r"\*\*Files to modify:\*\*\s*\n((?:\s*-\s*.+\n?)+)", content
        )
        if files_section:
            for file_line in files_section.group(1).splitlines():
                file_match = re.search(r"-\s*`?([^`\n]+)`?", file_line)
                if file_match:
                    files.append(file_match.group(1).strip())

        # Extract acceptance criteria
        acceptance = ""
        acc_match = re.search(
            r"\*\*Acceptance Criteria:\*\*\s*\n?(.*?)(?=\*\*|\Z)", content, re.DOTALL
        )
        if acc_match:
            acceptance = acc_match.group(1).strip()

        # Build description from remaining content
        description = content

        story = DecomposedStory(
            layer=layer,
            title=title_line,
            description=description,
            acceptance=acceptance,
            files=files,
            order=order,
            confidence=confidence,
        )
        stories.append(story)

    logger.info(f"Extracted {len(stories)} stories from comment on {issue_key}")
    return stories


def build_story_description(story: DecomposedStory) -> str:
    """
    Build Jira Story description from DecomposedStory.

    Args:
        story: DecomposedStory to format

    Returns:
        Markdown description for Jira
    """
    lines = [
        f"## {story.title}",
        "",
        story.description,
        "",
    ]
    if story.files:
        lines.append("### Files")
        for f in story.files:
            lines.append(f"- `{f}`")
        lines.append("")
    if story.acceptance:
        lines.append("### Acceptance Criteria")
        lines.append(story.acceptance)
        lines.append("")
    if story.confidence > 0:
        lines.append(f"**Confidence:** {story.confidence:.0%}")
        if story.confidence_flags:
            for flag in story.confidence_flags:
                lines.append(f"- {flag}")
        lines.append("")

    return "\n".join(lines)


def ensure_review_blocking_link(
    mcp: MCPClientManager,
    review_key: str,
    feature_key: str,
    config: dict,
) -> bool:
    """
    Ensure the blocking link exists: Feature IS_BLOCKED_BY Review story.

    Jira ignores duplicate links, so this is safe to call unconditionally.

    Returns:
        True if link was created/verified, False on failure
    """
    blocking_link_type = get_blocking_link_type(config)
    try:
        mcp.jira_link_issues(
            from_key=feature_key,
            to_key=review_key,
            link_type=blocking_link_type,
        )
        logger.info(f"Blocking link ensured: {feature_key} is blocked by {review_key}")
        return True
    except Exception as e:
        logger.warning(f"Failed to ensure blocking link {feature_key} -> {review_key}: {e}")
        return False


def create_jira_stories(
    mcp: MCPClientManager,
    parent_key: str,
    project_key: str,
    stories: list[DecomposedStory],
    config: dict,
    output_dir: str = "outputs",
) -> StoryCreationReport:
    """
    Create Jira Story issues and link to parent Feature (idempotent).

    Duplicate detection:
    1. Load manifest from previous run — skip stories already created
    2. Query existing child stories via JQL — skip exact summary matches
    3. Create only genuinely new stories

    Args:
        mcp: MCP client manager
        parent_key: Parent Feature key
        project_key: Jira project key
        stories: List of DecomposedStory objects
        config: SDLC config dict
        output_dir: Output directory for manifest file

    Returns:
        StoryCreationReport with per-story outcomes
    """
    parent_link_type = config.get("jira", {}).get("parent_link_type", "Parent")
    report = StoryCreationReport()

    # --- Duplicate detection: manifest ---
    manifest = _load_manifest(parent_key, output_dir)
    manifest_stories: dict[int, dict] = {}
    for entry in manifest.get("stories", []):
        manifest_stories[entry.get("order", -1)] = entry

    # --- Duplicate detection: existing Jira children ---
    existing_children = _get_existing_child_summaries(mcp, parent_key)

    for story in stories:
        summary = f"[{story.layer}] {story.title}"
        if len(summary) > 255:
            summary = summary[:252] + "..."

        # Check 1: Already in manifest with valid key
        manifest_entry = manifest_stories.get(story.order)
        if manifest_entry and manifest_entry.get("status") == "created":
            existing_key = manifest_entry.get("jira_key")
            if existing_key:
                logger.info(f"Story already exists (manifest): {existing_key}, skipping")
                report.outcomes.append(
                    StoryCreationOutcome(
                        story=story, jira_key=existing_key, status="skipped"
                    )
                )
                continue

        # Check 2: Existing child in Jira with matching summary
        match_key = existing_children.get(summary.lower())
        if match_key:
            logger.info(f"Story already exists (Jira child): {match_key}, skipping")
            report.outcomes.append(
                StoryCreationOutcome(
                    story=story, jira_key=match_key, status="skipped"
                )
            )
            continue

        # Create new story
        description = build_story_description(story)

        try:
            result = mcp.jira_create_issue(
                project_key=project_key,
                issue_type="Story",
                summary=summary,
                description=description,
                parent_key=parent_key,
            )

            key_match = re.search(r"([A-Z][A-Z0-9]*-\d+)", result)
            if not key_match:
                logger.error(f"Could not parse issue key from: {result}")
                report.outcomes.append(
                    StoryCreationOutcome(
                        story=story, status="failed",
                        error=f"Could not parse key from: {result[:100]}",
                    )
                )
                continue

            story_key = key_match.group(1)
            parent_field_ok = "PARENT_FIELD_FAILED" not in result
            logger.info(f"Created story: {story_key} - {summary} (parent_field={'ok' if parent_field_ok else 'failed'})")

            # Fallback: if parent field was rejected (Classic Jira / scheme mismatch),
            # establish relationship via issueLink
            if not parent_field_ok:
                try:
                    mcp.jira_link_issues(
                        from_key=parent_key,
                        to_key=story_key,
                        link_type=parent_link_type,
                    )
                    logger.info(f"Linked {story_key} as child of {parent_key} via issueLink")
                except Exception as e:
                    logger.warning(f"Failed to link {story_key} to {parent_key}: {e}")

            report.outcomes.append(
                StoryCreationOutcome(
                    story=story, jira_key=story_key, status="created"
                )
            )

        except Exception as e:
            logger.error(f"Failed to create story '{summary}': {e}")
            report.outcomes.append(
                StoryCreationOutcome(
                    story=story, status="failed", error=str(e)
                )
            )

    # Save manifest for resume-on-failure
    _save_manifest(parent_key, report, output_dir)

    return report


def create_dependency_links(
    mcp: MCPClientManager,
    created_stories: list[tuple[DecomposedStory, str]],
    config: dict | None = None,
) -> int:
    """
    Create blocking links between dependent stories.

    For each story that has depends_on, creates a "Blocks" link
    from the dependency to the dependent story.

    Args:
        mcp: MCP client manager
        created_stories: List of (story, jira_key) tuples from create_jira_stories
        config: SDLC config dict (optional, for blocking_link_type)

    Returns:
        Number of links created
    """
    # Build order -> key mapping
    order_to_key = {story.order: key for story, key in created_stories}
    blocking_link_type = get_blocking_link_type(config)

    links_created = 0
    for story, story_key in created_stories:
        for dep_order in story.depends_on:
            dep_key = order_to_key.get(dep_order)
            if not dep_key:
                logger.warning(
                    f"Cannot create dependency link: Step {dep_order} not found "
                    f"(required by {story_key})"
                )
                continue

            try:
                mcp.jira_link_issues(
                    from_key=story_key,
                    to_key=dep_key,
                    link_type=blocking_link_type,
                )
                logger.info(f"Created dependency link: {story_key} is blocked by {dep_key}")
                links_created += 1
            except Exception as e:
                logger.warning(
                    f"Failed to create dependency link {story_key} -> {dep_key}: {e}"
                )

    return links_created
