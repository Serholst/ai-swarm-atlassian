"""Markdown formatting utilities for Jira comments."""


def format_jira_panel(title: str, content: str, border_color: str = "#ccc") -> str:
    """
    Format content as Jira panel macro.

    Jira uses wiki markup for panels:
    {panel:title=Title|borderColor=#ccc}
    Content here
    {panel}

    Args:
        title: Panel title
        content: Panel content
        border_color: Border color (hex)

    Returns:
        Formatted panel macro
    """
    return f"""{{panel:title={title}|borderColor={border_color}}}
{content.strip()}
{{panel}}"""


def format_cot_panel(context: str, decision: str, alternatives: str = "") -> str:
    """
    Format Chain of Thoughts panel for Executor Rationale.

    Required by SDLC template for logging key decisions.

    Args:
        context: Context description
        decision: Decision made
        alternatives: Alternatives considered (optional)

    Returns:
        Formatted CoT panel
    """
    content_parts = [
        f"**Context:** {context}",
        f"**Decision:** {decision}",
    ]

    if alternatives:
        content_parts.append(f"**Alternatives Discarded:** {alternatives}")

    content = "\n".join(content_parts)

    return format_jira_panel(title="Executor Rationale", content=content)


def format_draft_comment_header(
    issue_key: str, slug: str, artifact_type: str = "architecture"
) -> str:
    """
    Format draft comment header per SDLC naming convention.

    Pattern: ### DRAFT: {Issue_Key}_{Slug}_{Artifact_Type}.md

    Args:
        issue_key: Jira issue key (e.g., AI-123)
        slug: Short project slug
        artifact_type: Type of artifact (default: architecture)

    Returns:
        Formatted header
    """
    return f"### DRAFT: {issue_key}_{slug}_{artifact_type}.md"


def format_story_list(stories: list[dict[str, str]]) -> str:
    """
    Format list of Stories for Jira comment.

    Args:
        stories: List of story dicts with 'layer' and 'title' keys

    Returns:
        Formatted Markdown list
    """
    lines = []
    for story in stories:
        layer = story.get("layer", "GEN")
        title = story.get("title", "Untitled")
        lines.append(f"- *[{layer}]* {title}")

    return "\n".join(lines)


def format_jira_markdown(content: str) -> str:
    """
    Format Markdown for Jira rendering.

    Jira has specific quirks with Markdown rendering:
    - Use h2/h3 for headers (## / ###)
    - Lists with - (not *)
    - Code blocks with {code}...{code} or ```

    Args:
        content: Raw markdown

    Returns:
        Jira-compatible markdown
    """
    # Ensure headers use ## format (not #)
    # Jira renders ## better than single #

    # Ensure lists use - (not *)
    content = content.replace("\n* ", "\n- ")

    return content.strip()
