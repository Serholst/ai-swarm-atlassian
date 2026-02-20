"""Shared template compliance section builder for LLM prompts."""

from ..models.execution_context import ConfluenceTemplate


def build_template_compliance_section(templates: list[ConfluenceTemplate]) -> str:
    """
    Build the template compliance section for the prompt.

    When templates are found, instructs the LLM to strictly follow
    the template structure in its work area recommendations.

    Args:
        templates: List of Confluence templates retrieved from the Templates folder

    Returns:
        Formatted markdown section string (empty string if no templates)
    """
    if not templates:
        return ""

    sections = [
        "",
        "---",
        "",
        "## TEMPLATE COMPLIANCE (MANDATORY)",
        "",
        "The following Confluence templates define the EXACT structure for documentation pages. "
        "When creating or updating these pages (DOCS layer), "
        "you MUST follow the exact structure, headings, sections, and macro placements from these templates. "
        "Do NOT invent arbitrary headings or sections — follow the template exactly.",
        "",
    ]

    for tmpl in templates:
        sections.append(f"### Template: {tmpl.doc_type}")
        sections.append(f"*Source: {tmpl.title}*")
        sections.append("")
        sections.append("```")
        sections.append(tmpl.content)
        sections.append("```")
        sections.append("")

    return "\n".join(sections)
