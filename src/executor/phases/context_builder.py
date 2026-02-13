"""
Context Builder - Stages 1-4 of the execution pipeline.

Stage 1: Trigger - Validate issue key
Stage 2: Jira Enrichment - Extract all relevant fields
Stage 3: Confluence Knowledge - Two-Stage Retrieval (API Search → LLM Reranking)
Stage 4: Data Aggregation - Build unified ExecutionContext
"""

import re
import json
import logging
from datetime import datetime
from typing import Optional

from ..mcp.client import MCPClientManager
from ..models.execution_context import (
    JiraContext,
    ConfluenceContext,
    ExecutionContext,
    ProjectStatus,
    ContextLocationError,
    RefinedDocument,
    RefinedConfluenceContext,
    SelectionLog,
)
from ..models.github_models import (
    GitHubContext,
    RepoStatus,
    RepoStructure,
    ConfigSummary,
    CodeSnippet,
)

logger = logging.getLogger(__name__)


# =============================================================================
# LLM Filter Prompts (DeepSeek - CTO Role)
# =============================================================================

FILTER_SYSTEM_PROMPT = """You are a Chief Technical Officer responsible for selecting relevant technical documentation for a development task.

## Your Role
Select ONLY documents that provide implementation details, API contracts, architectural constraints, or integration requirements directly relevant to the task.

## Selection Criteria
- ✅ SELECT: API specs, integration guides, architecture decisions, contract definitions
- ❌ REJECT: General overviews, meeting notes, status updates, unrelated modules

## Output Format
Return a JSON object with selected page IDs:
{
  "selected_ids": ["page_id_1", "page_id_2", "page_id_3"]
}

If no documents are relevant, return: {"selected_ids": []}"""


def build_filter_prompt(jira_summary: str, jira_description: str, candidates: list[dict]) -> str:
    """Build user prompt for DeepSeek document filtering."""
    candidate_lines = []
    for doc in candidates:
        excerpt = doc.get("excerpt", "")[:500].replace("\n", " ").strip()
        candidate_lines.append(
            f"- ID: `{doc['id']}` | Title: {doc['title']}\n"
            f"  Excerpt: {excerpt or '[No excerpt]'}"
        )

    candidates_text = "\n".join(candidate_lines)

    return f"""## Task

**Summary:** {jira_summary}

**Description:**
{jira_description}

---

## Candidates ({len(candidates)} pages)

{candidates_text}

---

Select relevant page IDs. Return JSON only."""


# =============================================================================
# Stage 1: Trigger - Validate and parse issue key
# =============================================================================

def parse_issue_key(task_input: str) -> str:
    """
    Parse Jira issue key from various input formats.

    Args:
        task_input: Issue key or URL (e.g., WEB3-6 or https://x.atlassian.net/browse/WEB3-6)

    Returns:
        Validated issue key (e.g., WEB3-6)

    Raises:
        ValueError: If input cannot be parsed
    """
    # Direct key format: PROJECT-123 or WEB3-6
    if re.match(r"^[A-Z][A-Z0-9]*-\d+$", task_input.upper()):
        return task_input.upper()

    # Extract from URL
    match = re.search(r"([A-Z][A-Z0-9]*-\d+)", task_input.upper())
    if match:
        return match.group(1)

    raise ValueError(f"Could not parse Jira issue key from: {task_input}")


# =============================================================================
# Stage 2: Jira Enrichment - Extract context from Jira
# =============================================================================

def extract_jira_context(mcp: MCPClientManager, issue_key: str) -> JiraContext:
    """
    Extract enriched context from Jira issue.

    Args:
        mcp: MCP client manager
        issue_key: Jira issue key (e.g., WEB3-6)

    Returns:
        JiraContext with all extracted fields
    """
    logger.info(f"Stage 2: Extracting Jira context for {issue_key}")

    # Get raw issue data via MCP
    # Note: jira_get_issue returns formatted markdown string
    # We need to also get structured data, so we'll parse the response
    raw_response = mcp.jira_get_issue(issue_key)

    # Parse the markdown response to extract fields
    # Format from jira_server.py:
    # # KEY: Summary
    # **Type:** X
    # **Status:** X
    # **Project:** Name (KEY)
    # **Assignee:** X
    # **Labels:** X
    # ## Description
    # ...
    # ## Metadata
    # - Created: X
    # - Updated: X
    # - Parent: X
    # - Subtasks: X

    context = _parse_jira_response(issue_key, raw_response)

    # Fetch comments for the issue
    try:
        comments_response = mcp.jira_get_comments(issue_key)
        context.comments = _parse_jira_comments(comments_response)
        logger.info(f"Stage 2: Retrieved {len(context.comments)} comments")
    except Exception as e:
        logger.warning(f"Failed to get comments: {e}")
        context.comments = []

    # Derive Confluence space key from labels or project
    context.confluence_space_key = _derive_confluence_space(context)

    logger.info(f"Stage 2 complete: {issue_key} -> space '{context.confluence_space_key}'")
    return context


def _parse_jira_response(issue_key: str, response: str) -> JiraContext:
    """Parse formatted Jira response into JiraContext."""

    def extract_field(pattern: str, text: str, default: str = "") -> str:
        match = re.search(pattern, text, re.IGNORECASE)
        return match.group(1).strip() if match else default

    def extract_list(pattern: str, text: str) -> list[str]:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            value = match.group(1).strip()
            if value and value.lower() != "none":
                return [item.strip() for item in value.split(",")]
        return []

    # Extract summary from title line: # KEY: Summary
    summary_match = re.search(r"^#\s*" + re.escape(issue_key) + r":\s*(.+)$", response, re.MULTILINE)
    summary = summary_match.group(1).strip() if summary_match else ""

    # Extract basic fields
    issue_type = extract_field(r"\*\*Type:\*\*\s*(.+?)(?:\n|$)", response)
    status = extract_field(r"\*\*Status:\*\*\s*(.+?)(?:\n|$)", response)
    assignee = extract_field(r"\*\*Assignee:\*\*\s*(.+?)(?:\n|$)", response)
    if assignee.lower() == "unassigned":
        assignee = None

    # Extract project: "Name (KEY)"
    project_match = re.search(r"\*\*Project:\*\*\s*(.+?)\s*\(([A-Z0-9]+)\)", response)
    project_name = project_match.group(1).strip() if project_match else ""
    project_key = project_match.group(2).strip() if project_match else issue_key.split("-")[0]

    # Extract project folder (custom "Project" field - Confluence folder name)
    project_folder = extract_field(r"\*\*Project Folder:\*\*\s*(.+?)(?:\n|$)", response)
    if project_folder.lower() == "none":
        project_folder = ""

    # Extract project link (custom "Project Link" field - direct Confluence URL)
    project_link = extract_field(r"\*\*Project Link:\*\*\s*(.+?)(?:\n|$)", response)
    if project_link.lower() == "none":
        project_link = ""

    # Extract labels
    labels = extract_list(r"\*\*Labels:\*\*\s*(.+?)(?:\n|$)", response)

    # Extract description (between ## Description and ## Metadata)
    desc_match = re.search(r"## Description\s*\n(.*?)(?=\n## Metadata|\Z)", response, re.DOTALL)
    description = desc_match.group(1).strip() if desc_match else ""

    # Extract metadata
    created = extract_field(r"-\s*Created:\s*(.+?)(?:\n|$)", response)
    updated = extract_field(r"-\s*Updated:\s*(.+?)(?:\n|$)", response)
    parent_key = extract_field(r"-\s*Parent:\s*(.+?)(?:\n|$)", response)
    if parent_key.lower() == "none":
        parent_key = None

    subtasks_str = extract_field(r"-\s*Subtasks:\s*(.+?)(?:\n|$)", response)
    subtasks = []
    if subtasks_str and subtasks_str.lower() != "none":
        subtasks = [s.strip() for s in subtasks_str.split(",")]

    return JiraContext(
        issue_key=issue_key,
        issue_id="",  # Not available in formatted response
        summary=summary,
        description=description,
        issue_type=issue_type,
        status=status,
        project_key=project_key,
        project_name=project_name,
        components=[],  # Not in current response format
        labels=labels,
        assignee=assignee,
        reporter=None,  # Not in current response format
        parent_key=parent_key,
        subtasks=subtasks,
        created=created,
        updated=updated,
        project_folder=project_folder,
        project_link=project_link,
    )


def _parse_jira_comments(response: str) -> list[dict]:
    """
    Parse Jira comments response into list of comment dicts.

    Response format from jira_server.py:
    Comments for KEY:

    ### Author Name - 2024-01-15T10:30:00.000+0000

    Comment body text here

    ### Another Author - 2024-01-16T14:00:00.000+0000

    Another comment body
    """
    comments = []

    if not response or "Comments for" not in response:
        return comments

    # Split by comment headers (### Author - Date)
    comment_blocks = re.split(r"\n### ", response)

    for block in comment_blocks[1:]:  # Skip the "Comments for KEY:" header
        lines = block.strip().split("\n")
        if not lines:
            continue

        # First line: "Author Name - 2024-01-15T10:30:00.000+0000"
        header = lines[0]
        header_match = re.match(r"(.+?)\s*-\s*(\d{4}-\d{2}-\d{2}.*?)$", header)

        if header_match:
            author = header_match.group(1).strip()
            created = header_match.group(2).strip()
            # Rest is the body
            body = "\n".join(lines[1:]).strip()

            comments.append({
                "author": author,
                "created": created,
                "body": body,
            })

    return comments


def _derive_confluence_space(jira: JiraContext) -> str:
    """
    Derive Confluence space key from Jira context.

    Priority:
    1. Label matching space pattern (lowercase)
    2. Project key
    3. Default "AI"
    """
    # Check labels for space identifier
    for label in jira.labels:
        # Labels like "web3", "ai", "platform" map to space keys
        if re.match(r"^[a-z][a-z0-9]*$", label.lower()):
            return label.upper()

    # Fallback to project key
    if jira.project_key:
        return jira.project_key

    # Default space
    return "AI"


# =============================================================================
# Stage 3: Confluence Knowledge Retrieval
# =============================================================================

def extract_confluence_context(
    mcp: MCPClientManager,
    space_key: str,
    sdlc_rules_title: str = "SDLC & Workflows Rules",
    project_name: Optional[str] = None,
) -> ConfluenceContext:
    """
    Extract knowledge from Confluence space.

    Args:
        mcp: MCP client manager
        space_key: Confluence space key
        sdlc_rules_title: Title of SDLC rules page
        project_name: Optional project name for passport lookup

    Returns:
        ConfluenceContext with retrieved content
    """
    logger.info(f"Stage 3: Retrieving Confluence knowledge for space '{space_key}'")

    context = ConfluenceContext(space_key=space_key)

    # 3.1 Get space root page (homepage)
    try:
        root_response = mcp.confluence_get_space_home(space_key)
        _parse_confluence_page_response(root_response, context, "root")
        logger.info(f"Stage 3.1: Retrieved space homepage")
    except Exception as e:
        error_msg = f"Failed to get space homepage: {e}"
        logger.warning(error_msg)
        context.retrieval_errors.append(error_msg)

    # 3.2 Get SDLC Rules page
    try:
        # First try in current space
        cql = f'space = "{space_key}" AND title ~ "SDLC"'
        sdlc_response = mcp.confluence_search_pages(cql, limit=1)

        if "Found 0 pages" in sdlc_response:
            # Fallback: Try global AI space
            cql = f'title = "{sdlc_rules_title}"'
            sdlc_response = mcp.confluence_search_pages(cql, limit=1)

        if "Found 0 pages" not in sdlc_response:
            # Get full page content
            _parse_confluence_search_response(sdlc_response, context, "sdlc")
            logger.info(f"Stage 3.2: Retrieved SDLC rules")
        else:
            context.retrieval_errors.append("SDLC rules page not found")

    except Exception as e:
        error_msg = f"Failed to get SDLC rules: {e}"
        logger.warning(error_msg)
        context.retrieval_errors.append(error_msg)

    # 3.3 Optional: Get Project Passport
    if project_name:
        try:
            passport_response = mcp.confluence_search_pages(
                f'space = "{space_key}" AND title ~ "Project Passport"',
                limit=1
            )
            if "Found 0 pages" not in passport_response:
                _parse_confluence_search_response(passport_response, context, "passport")
                logger.info(f"Stage 3.3: Retrieved Project Passport")
        except Exception as e:
            logger.debug(f"Project Passport not found: {e}")

    logger.info(f"Stage 3 complete: {len(context.retrieval_errors)} errors")
    return context


def _parse_confluence_page_response(response: str, context: ConfluenceContext, target: str) -> None:
    """Parse Confluence page response and update context."""
    # Response format from confluence_server.py:
    # # Title (Space Homepage)
    # **Space:** Name (KEY)
    # **URL:** ...
    # ## Content
    # ...

    # Extract title
    title_match = re.search(r"^#\s*(.+?)(?:\s*\(Space Homepage\))?$", response, re.MULTILINE)
    title = title_match.group(1).strip() if title_match else ""

    # Extract space name
    space_match = re.search(r"\*\*Space:\*\*\s*(.+?)\s*\(", response)
    if space_match:
        context.space_name = space_match.group(1).strip()

    # Extract URL
    url_match = re.search(r"\*\*URL:\*\*\s*(.+?)(?:\n|$)", response)
    url = url_match.group(1).strip() if url_match else ""

    # Extract content (after ## Content)
    content_match = re.search(r"## Content\s*\n(.*)", response, re.DOTALL)
    content = content_match.group(1).strip() if content_match else response

    if target == "root":
        context.root_page_title = title
        context.root_page_content = content
        context.root_page_url = url
    elif target == "sdlc":
        context.sdlc_rules_title = title
        context.sdlc_rules_content = content
        context.sdlc_rules_url = url


def _parse_confluence_search_response(response: str, context: ConfluenceContext, target: str) -> None:
    """Parse Confluence search response and update context."""
    # Search response format:
    # Found N pages:
    # - **Title** (SPACE) - [View](URL)
    #   Version: X, Labels: ...

    # For search results, we get summary not full content
    # Extract first result info
    title_match = re.search(r"\*\*(.+?)\*\*\s*\(", response)
    title = title_match.group(1).strip() if title_match else ""

    url_match = re.search(r"\[View\]\((.+?)\)", response)
    url = url_match.group(1).strip() if url_match else ""

    # Content from search is limited, but we store what we have
    # Strip the header lines
    lines = response.split("\n")
    content_lines = [l for l in lines if not l.startswith("Found ") and not l.startswith("- **")]
    content = "\n".join(content_lines).strip()

    if target == "sdlc":
        context.sdlc_rules_title = title
        context.sdlc_rules_content = content or f"[See full content at: {url}]"
        context.sdlc_rules_url = url
    elif target == "passport":
        context.project_passport_content = content or f"[See full content at: {url}]"
        context.project_passport_url = url


# =============================================================================
# Stage 3 (Enhanced): Two-Stage Retrieval with LLM Filtering
# =============================================================================

def get_refined_context(
    mcp: MCPClientManager,
    llm_client,  # DeepSeek client (OpenAI compatible)
    jira_id: str,
    jira_text: str,
    space_key: str,
    project_folder: str,
    project_link: str = "",
    config: Optional[dict] = None,
) -> RefinedConfluenceContext:
    """
    Two-Stage Retrieval: Confluence API Search → LLM Reranking (DeepSeek).

    Args:
        mcp: MCP client manager
        llm_client: DeepSeek client (OpenAI compatible)
        jira_id: Jira issue key (e.g., "PROJ-123")
        jira_text: Combined summary and description
        space_key: Confluence space key (from Jira project, e.g., "WEB3")
        project_folder: Project folder name (from custom "Project" field)
        project_link: Direct Confluence URL (from custom "Project Link" field)
        config: Optional SDLC config dict

    Returns:
        RefinedConfluenceContext with core and supporting documents

    Key behaviors:
        - Empty folder = NEW_PROJECT (not an error)
        - Missing Passport/Architecture = NEW_PROJECT (not an error)
        - Pages are text-only
    """
    context = RefinedConfluenceContext(
        project_space=space_key,
        jira_task_id=jira_id,
    )

    # =========================================================================
    # Phase 1: Resolution (Location Strategy)
    # =========================================================================
    try:
        resolved_space, folder_id = _resolve_confluence_location(
            mcp, space_key, project_folder, project_link
        )
        context.project_space = resolved_space
    except ContextLocationError as e:
        logger.error(f"Phase 1: Location not found - {e}")
        context.project_status = ProjectStatus.NOT_FOUND
        raise

    # Handle brand-new project (no Confluence location)
    if folder_id is None:
        context.project_status = ProjectStatus.BRAND_NEW
        context.missing_critical_data = [
            "Project Passport (needs creation)",
            "Logical Architecture (needs creation)",
            "Confluence project folder (needs creation)",
        ]
        logger.info("Phase 1: BRAND NEW project detected (no Confluence location)")
        # Skip Phases 2-3 (no docs to retrieve)
        return context

    # =========================================================================
    # Phase 2: Mandatory Path (Project Passport + Logical Architecture)
    # =========================================================================
    mandatory_docs = {
        "Project Passport": ["Project Passport", "Passport", "Паспорт проекта"],
        "Logical Architecture": ["Logical Architecture", "System Architecture", "Архитектура"],
    }

    found_mandatory = {"Project Passport": False, "Logical Architecture": False}

    for doc_type, keywords in mandatory_docs.items():
        for keyword in keywords:
            try:
                cql = f'ancestor = {folder_id} AND title ~ "{keyword}"'
                results = mcp.confluence_search_pages(cql, limit=3)

                if "Found 0 pages" not in results:
                    pages = _parse_search_results(results)
                    if pages:
                        page = pages[0]
                        full_page = mcp.confluence_get_page(page_id=page["id"])
                        content = _extract_text_content(full_page)

                        context.core_documents.append(RefinedDocument(
                            title=page["title"],
                            url=page["url"],
                            content=content,
                            id=page["id"],
                        ))
                        found_mandatory[doc_type] = True
                        logger.info(f"Phase 2: Found {doc_type} - '{page['title']}'")
                        break

            except Exception as e:
                logger.warning(f"Phase 2: Error searching '{keyword}': {e}")

    # Determine project status
    if not found_mandatory["Project Passport"] and not found_mandatory["Logical Architecture"]:
        context.project_status = ProjectStatus.NEW_PROJECT
        context.missing_critical_data = ["Project Passport", "Logical Architecture"]
        logger.info("Phase 2: NEW PROJECT detected (no mandatory docs)")
    elif not found_mandatory["Project Passport"]:
        context.missing_critical_data.append("Project Passport")
    elif not found_mandatory["Logical Architecture"]:
        context.missing_critical_data.append("Logical Architecture")

    # =========================================================================
    # Phase 3: Discovery Path (LLM Filter via DeepSeek)
    # =========================================================================
    if context.project_status == ProjectStatus.NEW_PROJECT:
        logger.info("Phase 3: Skipped (new project)")
    else:
        # Step 3.1: Broad CQL search
        keywords_from_jira = _extract_search_keywords(jira_text)
        cql = f'ancestor = {folder_id} AND (text ~ "{keywords_from_jira}")'

        try:
            search_results = mcp.confluence_search_pages(cql, limit=20)
            candidates = _parse_search_results_with_excerpts(search_results)

            # Filter out already-fetched core docs
            core_ids = {doc.id for doc in context.core_documents}
            candidates = [c for c in candidates if c["id"] not in core_ids]

            logger.info(f"Phase 3.1: {len(candidates)} candidates after filtering")

        except Exception as e:
            logger.warning(f"Phase 3.1: Search failed - {e}")
            candidates = []

        if candidates:
            # Step 3.2: LLM Filtering (DeepSeek)
            try:
                selection_log = _llm_filter_documents_deepseek(
                    llm_client=llm_client,
                    jira_summary=jira_text.split("\n")[0],
                    jira_description=jira_text,
                    candidates=candidates,
                )
                context.selection_log = selection_log
                selected_ids = selection_log.selected_ids
                logger.info(f"Phase 3.2: DeepSeek selected {len(selected_ids)} IDs")

                # Step 3.3: Fetch selected documents
                for page_id in selected_ids:
                    try:
                        full_page = mcp.confluence_get_page(page_id=page_id)
                        content = _extract_text_content(full_page)

                        candidate = next((c for c in candidates if c["id"] == page_id), {})

                        context.supporting_documents.append(RefinedDocument(
                            title=candidate.get("title", "Unknown"),
                            url=candidate.get("url", ""),
                            content=content,
                            id=page_id,
                        ))
                    except Exception as e:
                        logger.warning(f"Phase 3.3: Failed to fetch {page_id}: {e}")

            except Exception as e:
                logger.warning(f"Phase 3.2: DeepSeek filtering failed - {e}")
                context.retrieval_errors.append(f"LLM filtering failed: {e}")
        else:
            logger.info("Phase 3: No candidates to filter")

    # =========================================================================
    # Phase 4: Output Assembly
    # =========================================================================
    logger.info(
        f"Phase 4: Complete - status={context.project_status.value}, "
        f"core={len(context.core_documents)}, supporting={len(context.supporting_documents)}"
    )

    return context


def _extract_folder_id_from_url(url: str) -> str | None:
    """
    Extract Confluence page/folder ID from URL.

    Supported URL formats:
    - https://xxx.atlassian.net/wiki/spaces/SPACE/folder/123456
    - https://xxx.atlassian.net/wiki/spaces/SPACE/pages/123456/Title
    - https://xxx.atlassian.net/wiki/spaces/SPACE/pages/123456

    Args:
        url: Confluence URL

    Returns:
        Page/folder ID, or None if not found
    """
    if not url:
        return None

    # Pattern 1: /folder/{id}
    match = re.search(r'/folder/(\d+)', url)
    if match:
        return match.group(1)

    # Pattern 2: /pages/{id}/ or /pages/{id}
    match = re.search(r'/pages/(\d+)', url)
    if match:
        return match.group(1)

    # Pattern 3: pageId query parameter
    match = re.search(r'pageId=(\d+)', url)
    if match:
        return match.group(1)

    return None


def _resolve_confluence_location(
    mcp: MCPClientManager,
    space_key: str,
    project_folder: str,
    project_link: str = "",
) -> tuple[str, str | None]:
    """
    Resolve Confluence location using project link (preferred) or search.

    Priority:
    1. Direct URL from "Project Link" field - extract folder ID directly
    2. Search-based resolution (fallback for legacy issues)
    3. Brand-new project (no location) - returns None folder_id

    Args:
        mcp: MCP client manager
        space_key: Confluence space key (from Jira project key, e.g., WEB3)
        project_folder: Project folder name (from custom "Project" field)
        project_link: Direct Confluence URL (from custom "Project Link" field)

    Returns:
        (space_key, folder_id) - folder_id is None for brand-new projects

    Raises:
        ContextLocationError: If project_link provided but invalid
    """
    logger.info(f"Phase 1: Resolving location - link='{project_link}', folder='{project_folder}'")

    # Strategy 1: Direct URL (preferred - no API calls needed)
    if project_link:
        folder_id = _extract_folder_id_from_url(project_link)
        if folder_id:
            logger.info(f"Phase 1: Resolved from URL - folder_id={folder_id}")
            # Extract space from URL if possible, otherwise use provided space_key
            space_match = re.search(r'/spaces/([^/]+)/', project_link)
            if space_match:
                url_space = space_match.group(1).upper()
                logger.info(f"Phase 1: Using space from URL: {url_space}")
                return url_space, folder_id
            return space_key, folder_id
        else:
            logger.warning(f"Phase 1: Could not extract folder ID from URL: {project_link}")

    # Strategy 2: Search-based resolution (legacy fallback)
    if project_folder:
        logger.info(f"Phase 1: Falling back to search - space={space_key}, folder='{project_folder}'")

        try:
            # 2a: Exact title search
            cql = f'space = "{space_key}" AND title = "{project_folder}"'
            logger.info(f"Phase 1: Strategy 2a - exact title search: {cql}")

            results = mcp.confluence_search_pages(cql, limit=5)

            if "Found 0 pages" not in results:
                pages = _parse_search_results(results)
                if pages:
                    folder_id = pages[0]["id"]
                    logger.info(f"Phase 1: Resolved (exact match) - folder_id={folder_id}")
                    return space_key, folder_id

            # 2b: Fuzzy title search
            logger.info("Phase 1: Strategy 2b - fuzzy title search...")
            cql_fuzzy = f'space = "{space_key}" AND title ~ "{project_folder}"'
            results = mcp.confluence_search_pages(cql_fuzzy, limit=5)

            if "Found 0 pages" not in results:
                pages = _parse_search_results(results)
                if pages:
                    for page in pages:
                        if page["title"].lower() == project_folder.lower():
                            folder_id = page["id"]
                            logger.info(f"Phase 1: Resolved (fuzzy match) - folder_id={folder_id}")
                            return space_key, folder_id
                    # Use first result
                    folder_id = pages[0]["id"]
                    logger.info(f"Phase 1: Resolved (first fuzzy) - folder_id={folder_id}")
                    return space_key, folder_id

        except Exception as e:
            logger.warning(f"Phase 1: Search failed - {e}")

    # No location found - distinguish brand-new project from invalid link
    if not project_link and not project_folder:
        # Brand-new project: no Confluence location specified
        # Return None folder_id as sentinel for brand-new project
        logger.info("Phase 1: BRAND NEW project (no project_link, no project_folder)")
        return space_key, None

    # project_link provided but invalid, or project_folder search failed
    raise ContextLocationError(
        f"Could not resolve Confluence location. "
        f"Link: '{project_link}', Folder: '{project_folder}'. "
        f"Ensure 'Project Link' contains a valid Confluence URL."
    )


def _find_ancestor_by_name(ancestors_response: str, folder_name: str) -> str | None:
    """
    Find ancestor page ID by matching folder name in ancestors response.

    Args:
        ancestors_response: Response from confluence_get_page_ancestors
        folder_name: Name of the folder to find

    Returns:
        Page ID of matching ancestor, or None if not found
    """
    # Parse format: "1. [ID:123] Title"
    pattern = r'\[ID:(\d+)\]\s*(.+?)(?:\n|$)'

    for match in re.finditer(pattern, ancestors_response):
        ancestor_id = match.group(1)
        ancestor_title = match.group(2).strip()

        if ancestor_title.lower() == folder_name.lower():
            logger.info(f"Found matching ancestor: '{ancestor_title}' (ID: {ancestor_id})")
            return ancestor_id

    # Also check "Direct parent" line
    parent_match = re.search(r'Direct parent:\s*\[ID:(\d+)\]\s*(.+?)(?:\n|$)', ancestors_response)
    if parent_match:
        parent_id = parent_match.group(1)
        parent_title = parent_match.group(2).strip()
        if parent_title.lower() == folder_name.lower():
            logger.info(f"Found matching direct parent: '{parent_title}' (ID: {parent_id})")
            return parent_id

    return None


def _extract_page_id(response: str) -> str:
    """Extract page ID from Confluence response."""
    # Try URL pattern first
    url_match = re.search(r'/pages/(\d+)/', response)
    if url_match:
        return url_match.group(1)

    # Try pageId parameter
    id_match = re.search(r'pageId=(\d+)', response)
    if id_match:
        return id_match.group(1)

    return ""


def _extract_text_content(page_response: str) -> str:
    """
    Extract clean text content from Confluence page response.
    Text only - no image handling.
    """
    content_match = re.search(r"## Content.*?\n(.*)", page_response, re.DOTALL)
    if content_match:
        content = content_match.group(1).strip()
    else:
        content = page_response

    # Clean up excessive whitespace
    content = re.sub(r"\n{3,}", "\n\n", content)
    content = "\n".join(line.rstrip() for line in content.split("\n"))

    return content.strip()


def _extract_search_keywords(text: str, max_keywords: int = 5) -> str:
    """Extract search keywords from Jira text for CQL query."""
    terms = []

    # CamelCase terms (e.g., getUserProfile)
    terms += re.findall(r'\b[A-Z][a-z]+(?:[A-Z][a-z]+)+\b', text)

    # Acronyms (e.g., API, OAuth)
    terms += re.findall(r'\b[A-Z]{2,}\b', text)

    # Technical suffixes
    terms += re.findall(r'\b\w+(?:API|Service|Module|Handler|Client|Provider)\b', text, re.I)

    # Deduplicate and limit
    unique_terms = list(dict.fromkeys(terms))[:max_keywords]

    if not unique_terms:
        # Fallback: significant words
        words = [w for w in text.split() if len(w) > 4 and w.isalpha()][:max_keywords]
        unique_terms = words

    return " OR ".join(unique_terms) if unique_terms else text[:50]


def _parse_search_results(response: str) -> list[dict]:
    """Parse Confluence search response into list of page info."""
    pages = []

    logger.debug(f"Parsing search response:\n{response[:500]}...")

    # New format with explicit ID: - [ID:123] **Title** (SPACE) - [View](URL)
    pattern_with_id = r'\[ID:(\d+)\]\s*\*\*(.+?)\*\*\s*\([^)]+\)\s*-\s*\[View\]\(([^)]+)\)'

    for match in re.finditer(pattern_with_id, response):
        page_id = match.group(1)
        title = match.group(2)
        url = match.group(3)
        pages.append({
            "id": page_id,
            "title": title,
            "url": url,
        })
        logger.debug(f"Parsed page: id={page_id}, title={title}")

    # Fallback to old format: - **Title** (SPACE) - [View](URL)
    if not pages:
        pattern_legacy = r'\*\*(.+?)\*\*\s*\([^)]+\)\s*-\s*\[View\]\(([^)]+)\)'

        for match in re.finditer(pattern_legacy, response):
            title = match.group(1)
            url = match.group(2)
            # Extract page ID from URL (multiple patterns supported)
            page_id = ""

            # Pattern 1: /pages/{id}/ (most common)
            id_match = re.search(r'/pages/(\d+)/', url)
            if id_match:
                page_id = id_match.group(1)
            else:
                # Pattern 2: pageId query parameter
                id_match = re.search(r'pageId=(\d+)', url)
                if id_match:
                    page_id = id_match.group(1)
                else:
                    # Pattern 3: /pages/{id} at end of URL (no trailing slash)
                    id_match = re.search(r'/pages/(\d+)(?:\?|$)', url)
                    if id_match:
                        page_id = id_match.group(1)
                    else:
                        # Fallback: last segment of URL (may be title or encoded ID)
                        page_id = url.rstrip("/").split("/")[-1]
                        logger.warning(
                            f"Could not extract numeric page ID from URL: {url}, "
                            f"using fallback: {page_id}"
                        )

            pages.append({
                "id": page_id,
                "title": title,
                "url": url,
            })

    if not pages and "Found" in response and "pages" in response:
        logger.error(f"Search returned results but parsing failed. Response:\n{response}")

    return pages


def _parse_search_results_with_excerpts(response: str) -> list[dict]:
    """Parse search results including excerpts for LLM filtering."""
    pages = _parse_search_results(response)

    # Use title as excerpt placeholder (server doesn't return body excerpts)
    for page in pages:
        page["excerpt"] = f"Document titled: {page['title']}"

    return pages


def _llm_filter_documents_deepseek(
    llm_client,
    jira_summary: str,
    jira_description: str,
    candidates: list[dict],
) -> SelectionLog:
    """
    Call DeepSeek to filter document candidates.

    Args:
        llm_client: OpenAI-compatible client configured for DeepSeek
        jira_summary: Task summary
        jira_description: Full task description
        candidates: List of {id, title, excerpt}

    Returns:
        SelectionLog with full reasoning and selected IDs
    """
    # Log candidates for visibility
    logger.info("=" * 60)
    logger.info("🔍 DeepSeek Document Filtering")
    logger.info("=" * 60)
    logger.info(f"Task: {jira_summary[:80]}...")
    logger.info(f"Candidates ({len(candidates)}):")
    for c in candidates:
        logger.info(f"  - [{c['id']}] {c['title']}")
    logger.info("-" * 60)

    user_prompt = build_filter_prompt(jira_summary, jira_description, candidates)

    # Create base selection log (will be populated)
    selection_log = SelectionLog(
        system_prompt=FILTER_SYSTEM_PROMPT,
        user_prompt=user_prompt,
        candidates=candidates,
        raw_response="",
        selected_ids=[],
        model="deepseek-chat",
        tokens_used=0,
    )

    try:
        response = llm_client.chat.completions.create(
            model="deepseek-chat",
            messages=[
                {"role": "system", "content": FILTER_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.1,
            max_tokens=256,
        )

        raw_content = response.choices[0].message.content or ""
        tokens_used = response.usage.total_tokens if response.usage else 0
        logger.info(f"DeepSeek raw response: {raw_content}")

        # Update selection log with response
        selection_log.raw_response = raw_content
        selection_log.tokens_used = tokens_used

        # Extract JSON from response (handle markdown code blocks)
        json_str = raw_content
        if "```json" in json_str:
            json_str = json_str.split("```json")[1].split("```")[0]
        elif "```" in json_str:
            json_str = json_str.split("```")[1].split("```")[0]

        result = json.loads(json_str.strip())
        selected_ids = result.get("selected_ids", [])
        selection_log.selected_ids = selected_ids

        # Log selection results
        logger.info("-" * 60)
        logger.info(f"✅ DeepSeek Selected IDs: {selected_ids}")
        for c in candidates:
            status = "✓ SELECTED" if c["id"] in selected_ids else "✗ rejected"
            logger.info(f"  [{status}] {c['title']}")
        logger.info("=" * 60)

        return selection_log

    except json.JSONDecodeError as e:
        logger.error(f"DeepSeek invalid JSON: {e}")
        selection_log.raw_response += f"\n\n[ERROR: Invalid JSON - {e}]"
        return selection_log
    except Exception as e:
        logger.error(f"DeepSeek call failed: {e}")
        selection_log.raw_response = f"[ERROR: API call failed - {e}]"
        return selection_log


# =============================================================================
# Stage 3b: GitHub Context Extraction
# =============================================================================

def extract_github_url(text: str) -> str | None:
    """
    Extract GitHub repository URL from text (Jira description or Confluence).

    Args:
        text: Text to search for GitHub URLs

    Returns:
        GitHub repository URL or None if not found
    """
    # Match GitHub repository URLs
    # Patterns: https://github.com/owner/repo, git@github.com:owner/repo.git
    patterns = [
        r'https?://github\.com/([a-zA-Z0-9_-]+/[a-zA-Z0-9_.-]+?)(?:\.git)?(?:/|$|\s|\)|\])',
        r'git@github\.com:([a-zA-Z0-9_-]+/[a-zA-Z0-9_.-]+?)(?:\.git)?(?:\s|$)',
    ]

    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            repo_path = match.group(1).rstrip('/')
            return f"https://github.com/{repo_path}"

    return None


def parse_github_url(url: str) -> tuple[str, str]:
    """
    Parse owner and repo from GitHub URL.

    Args:
        url: GitHub repository URL

    Returns:
        (owner, repo) tuple
    """
    # Remove .git suffix if present
    url = url.rstrip('/').replace('.git', '')

    # Extract owner/repo from URL
    match = re.search(r'github\.com[/:]([a-zA-Z0-9_-]+)/([a-zA-Z0-9_.-]+)', url)
    if match:
        return match.group(1), match.group(2)

    raise ValueError(f"Could not parse GitHub URL: {url}")


def extract_confluence_topics(
    refined_confluence: RefinedConfluenceContext | None,
) -> set[str]:
    """
    Extract topic keywords from Confluence documents for deduplication.

    Args:
        refined_confluence: Confluence context with core and supporting docs

    Returns:
        Set of topic keywords found in Confluence
    """
    topics = set()

    if not refined_confluence:
        return topics

    # Keywords to detect in Confluence content
    topic_patterns = {
        "tech_stack": [r"technology\s+stack", r"tech\s+stack", r"dependencies", r"frameworks?"],
        "architecture": [r"architecture", r"system\s+design", r"component\s+diagram"],
        "api_contracts": [r"api\s+contract", r"api\s+spec", r"endpoints?", r"rest\s+api"],
        "database": [r"database", r"schema", r"data\s+model", r"entities"],
        "deployment": [r"deployment", r"infrastructure", r"kubernetes", r"docker"],
        "authentication": [r"authentication", r"authorization", r"oauth", r"jwt"],
    }

    # Combine all document content
    all_content = ""
    for doc in refined_confluence.core_documents + refined_confluence.supporting_documents:
        all_content += doc.content.lower() + " "

    # Check which topics are covered
    for topic, patterns in topic_patterns.items():
        for pattern in patterns:
            if re.search(pattern, all_content, re.IGNORECASE):
                topics.add(topic)
                break

    return topics


def extract_github_context(
    mcp: "MCPClientManager",
    jira_context: JiraContext,
    refined_confluence: RefinedConfluenceContext | None,
    llm_client=None,
    config: dict | None = None,
) -> GitHubContext:
    """
    Extract GitHub repository context with Confluence-based deduplication.

    Priority for repository URL:
    1. Jira issue description
    2. Confluence Project Passport

    Args:
        mcp: MCP client manager
        jira_context: Jira context with description
        refined_confluence: Confluence context for deduplication
        llm_client: Optional LLM client for code snippet selection
        config: Optional config dict

    Returns:
        GitHubContext with repository information
    """
    logger.info("Stage 3b: Extracting GitHub context")

    context = GitHubContext()

    # Check if GitHub MCP is available
    if not mcp.github_available():
        logger.info("Stage 3b: GitHub MCP not available - skipping")
        context.retrieval_errors.append("GitHub MCP client not available")
        return context

    # =========================================================================
    # Phase 1: Repository URL Discovery
    # =========================================================================

    # Priority 1: Extract from Jira description
    repo_url = extract_github_url(jira_context.description)
    if repo_url:
        context.discovery_source = "jira_description"
        logger.info(f"Stage 3b: Found GitHub URL in Jira description: {repo_url}")

    # Priority 2: Extract from Confluence Project Passport
    if not repo_url and refined_confluence:
        for doc in refined_confluence.core_documents:
            if "passport" in doc.title.lower():
                repo_url = extract_github_url(doc.content)
                if repo_url:
                    context.discovery_source = "confluence_passport"
                    logger.info(f"Stage 3b: Found GitHub URL in Project Passport: {repo_url}")
                    break

    # No repository found - new project
    if not repo_url:
        context.status = RepoStatus.NEW_PROJECT
        logger.info("Stage 3b: No GitHub URL found - marking as new project")
        return context

    context.repository_url = repo_url

    # Parse owner and repo
    try:
        owner, repo_name = parse_github_url(repo_url)
        context.owner = owner
        context.repo_name = repo_name
    except ValueError as e:
        context.status = RepoStatus.NOT_FOUND
        context.retrieval_errors.append(str(e))
        return context

    # =========================================================================
    # Phase 2: Confluence Deduplication Analysis
    # =========================================================================

    confluence_topics = extract_confluence_topics(refined_confluence)
    context.skipped_topics = list(confluence_topics)
    logger.info(f"Stage 3b: Confluence covers topics: {confluence_topics}")

    # =========================================================================
    # Phase 3: GitHub Data Retrieval
    # =========================================================================

    # 3.1: Verify repository exists by fetching root directory
    try:
        repo_info = mcp.github_get_repository(owner, repo_name)
        context.status = RepoStatus.EXISTS

        # Parse response for files/structure
        # The response contains directory listing when fetching root
        if repo_info:
            # Try to detect primary language from file extensions
            if ".py" in repo_info or "pyproject.toml" in repo_info:
                context.primary_language = "Python"
            elif "package.json" in repo_info:
                context.primary_language = "JavaScript/TypeScript"
            elif "Cargo.toml" in repo_info:
                context.primary_language = "Rust"
            elif "go.mod" in repo_info:
                context.primary_language = "Go"
            elif "pom.xml" in repo_info:
                context.primary_language = "Java"

        logger.info(f"Stage 3b: Repository accessible - language={context.primary_language}")

    except Exception as e:
        context.status = RepoStatus.NOT_FOUND
        context.retrieval_errors.append(f"Failed to access repository: {e}")
        logger.warning(f"Stage 3b: Repository not accessible: {e}")
        return context

    # 3.2: Get repository structure (already fetched in 3.1, reuse if available)
    try:
        # repo_info from 3.1 already contains root directory structure
        if repo_info:
            context.structure = _parse_repo_structure(repo_info)
            logger.info(f"Stage 3b: Got repository structure")
    except Exception as e:
        context.retrieval_errors.append(f"Failed to parse repository structure: {e}")
        logger.warning(f"Stage 3b: Failed to parse structure: {e}")

    # 3.3: Get config files (skip if covered in Confluence)
    config_files = ["package.json", "pyproject.toml", "Cargo.toml", "go.mod", "pom.xml"]

    for config_file in config_files:
        # Skip tech stack details if covered in Confluence
        if "tech_stack" in confluence_topics and config_file in ["package.json", "pyproject.toml"]:
            context.configs.append(ConfigSummary(
                path=config_file,
                summary="Tech stack documented in Confluence",
                in_confluence=True,
            ))
            continue

        try:
            content = mcp.github_get_file_contents(owner, repo_name, config_file)
            summary = _summarize_config_file(config_file, content)
            context.configs.append(ConfigSummary(
                path=config_file,
                summary=summary,
                in_confluence=False,
            ))
        except Exception:
            # File doesn't exist - skip silently
            pass

    # 3.4: Get recent commits (never in Confluence)
    try:
        commits_response = mcp.github_list_commits(owner, repo_name, per_page=10)
        context.recent_commits = _parse_commits(commits_response)
        logger.info(f"Stage 3b: Got {len(context.recent_commits)} recent commits")
    except Exception as e:
        context.retrieval_errors.append(f"Failed to get commits: {e}")

    # 3.5: Get open PRs (never in Confluence) - optional, may not be available
    try:
        prs_response = mcp.github_list_pull_requests(owner, repo_name, state="open")
        context.open_prs = _parse_pull_requests(prs_response)
        logger.info(f"Stage 3b: Got {len(context.open_prs)} open PRs")
    except Exception as e:
        # PR listing might not be available - not critical
        logger.debug(f"Stage 3b: PR listing not available: {e}")

    # 3.6: Search for relevant code snippets based on Jira keywords - optional
    if llm_client and "tech_stack" not in confluence_topics:
        try:
            keywords = _extract_search_keywords(jira_context.summary + " " + jira_context.description)
            query = f"repo:{owner}/{repo_name} {keywords}"
            search_results = mcp.github_search_code(query)
            context.snippets = _parse_code_search_results(search_results, mcp, owner, repo_name)
            logger.info(f"Stage 3b: Found {len(context.snippets)} relevant code snippets")
        except Exception as e:
            # Code search might not be available - not critical
            logger.debug(f"Stage 3b: Code search not available: {e}")

    logger.info(
        f"Stage 3b complete: status={context.status.value}, "
        f"configs={len(context.configs)}, snippets={len(context.snippets)}"
    )

    return context


def _parse_repo_structure(response: str) -> RepoStructure:
    """Parse repository structure from GitHub API response."""
    key_dirs = []
    files = []
    dirs = []

    # Common key directories to highlight
    key_patterns = ["src", "lib", "app", "api", "tests", "test", "config", "docs"]

    # Try to parse as JSON array (GitHub API format)
    try:
        import json
        # Response might be JSON array or formatted text
        if response.strip().startswith("["):
            items = json.loads(response)
        else:
            # Try to extract JSON from response
            json_match = re.search(r'\[[\s\S]*\]', response)
            if json_match:
                items = json.loads(json_match.group())
            else:
                items = []

        for item in items:
            name = item.get("name", "")
            item_type = item.get("type", "")
            path = item.get("path", name)

            if item_type == "dir":
                dirs.append(f"📁 {name}/")
                if name in key_patterns:
                    key_dirs.append(name)
            else:
                files.append(f"📄 {name}")

    except (json.JSONDecodeError, TypeError):
        # Fallback: parse as text
        for line in response.split("\n"):
            line = line.strip()
            if '"name":' in line:
                name_match = re.search(r'"name":\s*"([^"]+)"', line)
                if name_match:
                    name = name_match.group(1)
                    if '"type": "dir"' in response:
                        dirs.append(f"📁 {name}/")
                    else:
                        files.append(f"📄 {name}")

    # Build tree representation
    tree_lines = []
    if dirs:
        tree_lines.append("Directories:")
        tree_lines.extend(sorted(dirs)[:20])  # Limit directories
    if files:
        tree_lines.append("\nFiles:")
        tree_lines.extend(sorted(files)[:30])  # Limit files

    total_count = len(dirs) + len(files)
    if total_count > 50:
        tree_lines.append(f"\n... ({total_count - 50} more entries)")

    tree = "\n".join(tree_lines) if tree_lines else "Unable to parse structure"

    return RepoStructure(
        tree=tree,
        key_directories=key_dirs,
        file_count=total_count,
    )


def _summarize_config_file(filename: str, content: str) -> str:
    """Create a brief summary of a config file."""
    summary_parts = []

    if filename == "package.json":
        # Extract key info from package.json
        if '"name"' in content:
            name_match = re.search(r'"name":\s*"([^"]+)"', content)
            if name_match:
                summary_parts.append(f"name: {name_match.group(1)}")

        if '"version"' in content:
            version_match = re.search(r'"version":\s*"([^"]+)"', content)
            if version_match:
                summary_parts.append(f"v{version_match.group(1)}")

        # Count dependencies
        deps_count = content.count('"dependencies"')
        dev_deps_count = content.count('"devDependencies"')
        if deps_count or dev_deps_count:
            summary_parts.append("Node.js project")

    elif filename == "pyproject.toml":
        summary_parts.append("Python project (pyproject.toml)")

    elif filename == "Cargo.toml":
        summary_parts.append("Rust project (Cargo.toml)")

    elif filename == "go.mod":
        summary_parts.append("Go project (go.mod)")

    elif filename == "pom.xml":
        summary_parts.append("Java/Maven project (pom.xml)")

    return ", ".join(summary_parts) if summary_parts else f"Config file: {filename}"


def _parse_commits(response: str) -> list[str]:
    """Parse commit messages from GitHub API response."""
    commits = []

    # Try to extract commit messages from response
    # Format: "message": "commit message"
    for match in re.finditer(r'"message":\s*"([^"]+)"', response):
        message = match.group(1)
        # Take first line of commit message
        first_line = message.split("\\n")[0].strip()
        if first_line and len(first_line) < 200:
            commits.append(first_line)

    return commits[:10]  # Limit to 10


def _parse_pull_requests(response: str) -> list[str]:
    """Parse PR titles from GitHub API response."""
    prs = []

    # Try to extract PR titles from response
    for match in re.finditer(r'"title":\s*"([^"]+)"', response):
        title = match.group(1)
        if title and len(title) < 200:
            prs.append(title)

    return prs[:10]  # Limit to 10


def _parse_code_search_results(
    response: str,
    mcp: "MCPClientManager",
    owner: str,
    repo: str,
) -> list[CodeSnippet]:
    """Parse code search results and fetch snippets."""
    snippets = []

    # Extract file paths from search results
    paths = []
    for match in re.finditer(r'"path":\s*"([^"]+)"', response):
        path = match.group(1)
        if path not in paths:
            paths.append(path)

    # Fetch first 3 relevant files
    for path in paths[:3]:
        try:
            content = mcp.github_get_file_contents(owner, repo, path)
            # Take first 50 lines
            lines = content.split("\n")[:50]
            snippet_content = "\n".join(lines)

            snippets.append(CodeSnippet(
                path=path,
                lines="1-50",
                content=snippet_content,
                relevance="Matched search keywords from task",
            ))
        except Exception:
            pass

    return snippets


# =============================================================================
# Stage 4: Data Aggregation
# =============================================================================

def build_execution_context(
    issue_key: str,
    jira_context: JiraContext,
    confluence_context: Optional[ConfluenceContext] = None,
    refined_confluence: Optional[RefinedConfluenceContext] = None,
    github_context: Optional[GitHubContext] = None,
) -> ExecutionContext:
    """
    Aggregate Jira, Confluence, and GitHub data into unified ExecutionContext.

    Args:
        issue_key: Jira issue key
        jira_context: Stage 2 output
        confluence_context: Stage 3a output (legacy)
        refined_confluence: Stage 3a output (Two-Stage Retrieval)
        github_context: Stage 3b output (GitHub with Confluence deduplication)

    Returns:
        ExecutionContext ready for LLM execution
    """
    logger.info(f"Stage 4: Building execution context for {issue_key}")

    context = ExecutionContext(
        issue_key=issue_key,
        timestamp=datetime.now(),
        jira=jira_context,
        confluence=confluence_context,
        refined_confluence=refined_confluence,
        github=github_context,
    )

    # Validate minimum requirements
    if not context.is_valid():
        context.errors.append("Context validation failed: missing required Jira data")

    logger.info(f"Stage 4 complete: valid={context.is_valid()}")
    return context


# =============================================================================
# Full Pipeline: Stages 1-4
# =============================================================================

def build_context_pipeline(
    mcp: MCPClientManager,
    task_input: str,
    sdlc_rules_title: str = "SDLC & Workflows Rules",
) -> ExecutionContext:
    """
    Execute full context building pipeline (Stages 1-4) - Legacy version.

    Args:
        mcp: MCP client manager (must be started)
        task_input: Jira issue key or URL
        sdlc_rules_title: Title of SDLC rules page in Confluence

    Returns:
        ExecutionContext ready for Stage 5 (LLM execution)
    """
    # Stage 1: Parse and validate issue key
    issue_key = parse_issue_key(task_input)
    logger.info(f"Pipeline started for {issue_key}")

    # Stage 2: Jira enrichment
    jira_context = extract_jira_context(mcp, issue_key)

    # Stage 3: Confluence knowledge retrieval
    confluence_context = extract_confluence_context(
        mcp,
        space_key=jira_context.confluence_space_key,
        sdlc_rules_title=sdlc_rules_title,
        project_name=jira_context.project_name,
    )

    # Stage 4: Aggregation
    execution_context = build_execution_context(
        issue_key=issue_key,
        jira_context=jira_context,
        confluence_context=confluence_context,
    )

    return execution_context


def build_refined_context_pipeline(
    mcp: MCPClientManager,
    llm_client,
    task_input: str,
    config: Optional[dict] = None,
) -> ExecutionContext:
    """
    Execute full context building pipeline with Two-Stage Retrieval (Stages 1-4).

    Uses LLM-based document filtering for intelligent context selection.
    Includes GitHub context extraction with Confluence-based deduplication.

    Args:
        mcp: MCP client manager (must be started)
        llm_client: DeepSeek client (OpenAI compatible) for document filtering
        task_input: Jira issue key or URL
        config: Optional SDLC config dict

    Returns:
        ExecutionContext ready for Stage 5 (LLM execution)
    """
    # Stage 1: Parse and validate issue key
    issue_key = parse_issue_key(task_input)
    logger.info(f"Refined pipeline started for {issue_key}")

    # Stage 2: Jira enrichment
    jira_context = extract_jira_context(mcp, issue_key)

    # Stage 3a: Two-Stage Retrieval (Confluence: API Search → LLM Reranking)
    jira_text = f"{jira_context.summary}\n\n{jira_context.description}"

    logger.info(
        f"Stage 3a: space_key={jira_context.project_key}, "
        f"project_folder={jira_context.project_folder}, "
        f"project_link={jira_context.project_link}"
    )

    refined_confluence = get_refined_context(
        mcp=mcp,
        llm_client=llm_client,
        jira_id=issue_key,
        jira_text=jira_text,
        space_key=jira_context.project_key,
        project_folder=jira_context.project_folder,
        project_link=jira_context.project_link,
        config=config,
    )

    # Stage 3b: GitHub Context (with Confluence deduplication)
    github_context = None
    if mcp.github_available():
        logger.info("Stage 3b: Extracting GitHub context")
        github_context = extract_github_context(
            mcp=mcp,
            jira_context=jira_context,
            refined_confluence=refined_confluence,
            llm_client=llm_client,
            config=config,
        )
    else:
        logger.info("Stage 3b: GitHub MCP not available - skipping")

    # Stage 4: Aggregation
    execution_context = build_execution_context(
        issue_key=issue_key,
        jira_context=jira_context,
        refined_confluence=refined_confluence,
        github_context=github_context,
    )

    return execution_context
