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
            mcp, space_key, project_folder
        )
        context.project_space = resolved_space
    except ContextLocationError as e:
        logger.error(f"Phase 1: Location not found - {e}")
        context.project_status = ProjectStatus.NOT_FOUND
        raise

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


def _resolve_confluence_location(
    mcp: MCPClientManager,
    space_key: str,
    project_folder: str,
) -> tuple[str, str]:
    """
    Resolve Confluence location using space key and project folder.

    Args:
        mcp: MCP client manager
        space_key: Confluence space key (from Jira project key, e.g., WEB3)
        project_folder: Project folder name (from custom "Project" field)

    Returns:
        (space_key, folder_id)

    Raises:
        ContextLocationError: If folder cannot be found
    """
    logger.info(f"Phase 1: Resolving space={space_key}, folder={project_folder}")

    if not project_folder:
        raise ContextLocationError(
            f"Project folder not specified. Ensure Jira issue has 'Project' field set."
        )

    # Find folder by exact title match in the space
    cql = f'space = "{space_key}" AND title = "{project_folder}"'
    try:
        results = mcp.confluence_search_pages(cql, limit=1)

        if "Found 0 pages" in results:
            raise ContextLocationError(
                f"Folder '{project_folder}' not found in space '{space_key}'"
            )

        pages = _parse_search_results(results)
        if not pages:
            raise ContextLocationError(
                f"Could not parse search results for '{project_folder}'"
            )

        folder_id = pages[0]["id"]
        logger.info(f"Phase 1: Resolved - space={space_key}, folder_id={folder_id}")
        return space_key, folder_id

    except ContextLocationError:
        raise
    except Exception as e:
        raise ContextLocationError(
            f"Failed to resolve location: space={space_key}, folder={project_folder}: {e}"
        )


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
    # Pattern: - **Title** (SPACE) - [View](URL)
    pattern = r'\*\*(.+?)\*\*\s*\([^)]+\)\s*-\s*\[View\]\(([^)]+)\)'

    for match in re.finditer(pattern, response):
        title = match.group(1)
        url = match.group(2)
        # Extract page ID from URL (last segment or pageId param)
        page_id = ""
        id_match = re.search(r'/pages/(\d+)/', url) or re.search(r'pageId=(\d+)', url)
        if id_match:
            page_id = id_match.group(1)
        else:
            page_id = url.rstrip("/").split("/")[-1]

        pages.append({
            "id": page_id,
            "title": title,
            "url": url,
        })

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
# Stage 4: Data Aggregation
# =============================================================================

def build_execution_context(
    issue_key: str,
    jira_context: JiraContext,
    confluence_context: Optional[ConfluenceContext] = None,
    refined_confluence: Optional[RefinedConfluenceContext] = None,
) -> ExecutionContext:
    """
    Aggregate Jira and Confluence data into unified ExecutionContext.

    Args:
        issue_key: Jira issue key
        jira_context: Stage 2 output
        confluence_context: Stage 3 output (legacy)
        refined_confluence: Stage 3 output (Two-Stage Retrieval)

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

    # Stage 3: Two-Stage Retrieval (API Search → LLM Reranking)
    jira_text = f"{jira_context.summary}\n\n{jira_context.description}"

    logger.info(
        f"Stage 3: space_key={jira_context.project_key}, "
        f"project_folder={jira_context.project_folder}"
    )

    refined_confluence = get_refined_context(
        mcp=mcp,
        llm_client=llm_client,
        jira_id=issue_key,
        jira_text=jira_text,
        space_key=jira_context.project_key,
        project_folder=jira_context.project_folder,
        config=config,
    )

    # Stage 4: Aggregation
    execution_context = build_execution_context(
        issue_key=issue_key,
        jira_context=jira_context,
        refined_confluence=refined_confluence,
    )

    return execution_context
