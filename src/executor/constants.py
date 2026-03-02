"""
Shared constants and helpers for the executor pipeline.

Single source of truth for values duplicated across phases, prompts, and models.
Import from here instead of redefining.
"""

from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

# ── Layer taxonomy ────────────────────────────────────────────────────────────
# Canonical set of layer codes used across validation, decomposition, and status checks.
VALID_LAYERS: frozenset[str] = frozenset({"BE", "FE", "INFRA", "DB", "QA", "DOCS", "GEN"})

LAYER_RE: re.Pattern[str] = re.compile(
    r"\[(" + "|".join(sorted(VALID_LAYERS)) + r")\]"
)

# ── Content budget defaults (chars) for Confluence document truncation ────────
CORE_DOC_MAX_CHARS = 12000
SUPPORTING_DOC_MAX_CHARS = 6000

# ── Issue key helpers ─────────────────────────────────────────────────────────
_ISSUE_KEY_RE = re.compile(r"^[A-Z][A-Z0-9]+-\d+$")


def extract_project_key(issue_key: str) -> str:
    """Extract the project prefix from a Jira issue key (e.g. 'WEB3-6' → 'WEB3')."""
    return issue_key.split("-", 1)[0]


def validate_issue_key(issue_key: str) -> bool:
    """Return True if issue_key matches the Jira key format."""
    return bool(_ISSUE_KEY_RE.match(issue_key))


# ── Config access helpers ─────────────────────────────────────────────────────

def get_blocking_link_type(config: dict | None) -> str:
    """Read blocking link type from SDLC config with safe fallback."""
    if not config:
        return "Blocks"
    return config.get("jira", {}).get("blocking_link_type", "Blocks")


def get_parent_link_type(config: dict | None) -> str:
    """Read parent link type from SDLC config with safe fallback."""
    if not config:
        return "Parent"
    return config.get("jira", {}).get("parent_link_type", "Parent")


# ── Confluence → config enrichment ───────────────────────────────────────────

# Maps "Link Type" column values to config keys
_LINK_TYPE_CONFIG_MAP: dict[str, str] = {
    "blocking": "blocking_link_type",
    "parent": "parent_link_type",
}


def parse_link_types_table(page_content: str) -> dict[str, str]:
    """
    Parse a 'Link Types' markdown table from an SDLC Confluence page.

    Expected table format (under a '## Link Types' heading):

        | Link Type | Jira Name | Direction | From | To | Usage |
        |-----------|-----------|-----------|------|----|-------|
        | Blocking  | Blocks    | outward   | ...  | .. | ...   |
        | Parent    | Parent    | outward   | ...  | .. | ...   |

    Returns:
        dict mapping config keys to Jira link-type names,
        e.g. {"blocking_link_type": "Blocks", "parent_link_type": "Parent"}.
        Empty dict if the table is not found.
    """
    result: dict[str, str] = {}

    # Find the Link Types section
    section_match = re.search(
        r"#+\s*Link\s+Types\b",
        page_content,
        re.IGNORECASE,
    )
    if not section_match:
        return result

    # Extract content after the heading until the next heading or end
    rest = page_content[section_match.end():]
    next_heading = re.search(r"\n#+\s", rest)
    section = rest[: next_heading.start()] if next_heading else rest

    # Parse markdown table rows: | col1 | col2 | ...
    # Skip header row and separator row (---), process data rows
    rows = re.findall(r"^\|(.+)\|$", section, re.MULTILINE)
    if len(rows) < 3:  # header + separator + at least 1 data row
        return result

    # rows[0] = header, rows[1] = separator (---|---), rows[2:] = data
    for row in rows[2:]:
        cells = [c.strip() for c in row.split("|")]
        if len(cells) < 2:
            continue
        link_type_name = cells[0].lower()  # "blocking", "parent", "dependency"
        jira_name = cells[1]  # "Blocks", "Parent"

        config_key = _LINK_TYPE_CONFIG_MAP.get(link_type_name)
        if config_key and jira_name:
            result[config_key] = jira_name

    return result


def fetch_link_types_from_confluence(mcp, config_dict: dict) -> dict[str, str]:
    """
    Fetch SDLC page from Confluence and parse Link Types table.

    Uses CQL search by page title (no space_key required).
    Falls back gracefully — returns empty dict on any failure.

    Args:
        mcp: MCPClientManager (must be started)
        config_dict: SDLC config as dict (to read sdlc_rules_page_title)

    Returns:
        dict of config overrides, e.g. {"blocking_link_type": "Blocks"}
    """
    title = (
        config_dict.get("confluence", {})
        .get("sdlc_rules_page_title", "SDLC & Workflows Rules")
    )

    try:
        cql = f'title = "{title}"'
        search_result = mcp.confluence_search_pages(cql, limit=1)

        if not search_result or "Found 0 pages" in search_result:
            logger.debug("SDLC page '%s' not found in Confluence", title)
            return {}

        # Search result contains page ID — extract it and fetch full content
        page_id_match = re.search(r"ID:\s*(\d+)", search_result)
        if not page_id_match:
            logger.debug("Could not extract page ID from search result")
            return {}

        page_content = mcp.confluence_get_page(page_id=page_id_match.group(1))
        if not page_content:
            return {}

        link_types = parse_link_types_table(page_content)
        if link_types:
            logger.info(
                "Link types loaded from Confluence: %s",
                ", ".join(f"{k}={v}" for k, v in link_types.items()),
            )
        return link_types

    except Exception as exc:
        logger.debug("Failed to fetch link types from Confluence: %s", exc)
        return {}
