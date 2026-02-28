"""Shared template compliance section builder for LLM prompts."""

import re

from ..models.execution_context import ConfluenceTemplate

# Patterns that indicate structural lines worth keeping
_STRUCTURAL_PATTERNS = re.compile(
    r"^("
    r"#{1,6}\s"            # Markdown headings
    r"|[*-]\s\*\*"         # Bold list items (likely field names)
    r"|\|.*\|"             # Table rows
    r"|Section:|Field:"    # Explicit section/field markers
    r"|\{panel"            # Confluence panel macros
    r"|\{expand"           # Confluence expand macros
    r"|\{status"           # Confluence status macros
    r"|\{toc"              # Table of contents macro
    r")",
    re.IGNORECASE,
)

_MIN_STRUCTURE_LENGTH = 50  # Fallback to full content if extraction is too short


def _extract_template_structure(content: str) -> str:
    """Extract only structural elements (headings, fields, macros) from template content.

    Templates define page structure — headings and section names — not body content.
    Stripping instructional boilerplate and examples significantly reduces token usage.
    """
    structural_lines = []
    for line in content.split("\n"):
        stripped = line.strip()
        if not stripped:
            # Keep blank lines between structural elements for readability
            if structural_lines and structural_lines[-1] != "":
                structural_lines.append("")
            continue
        if _STRUCTURAL_PATTERNS.match(stripped):
            structural_lines.append(line)

    result = "\n".join(structural_lines).strip()

    # Fallback: if we extracted almost nothing, keep original
    if len(result) < _MIN_STRUCTURE_LENGTH:
        return content

    return result


def build_template_compliance_section(templates: list[ConfluenceTemplate]) -> str:
    """
    Build the template compliance section for the prompt.

    Extracts only structural elements (headings, macros, field markers) from
    templates to reduce token usage while preserving the page structure the LLM
    must follow.

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
        "you MUST follow the exact headings, sections, and macro placements from these templates. "
        "Do NOT invent arbitrary headings or sections — follow the template exactly.",
        "",
    ]

    for tmpl in templates:
        sections.append(f"### Template: {tmpl.doc_type}")
        sections.append(f"*Source: {tmpl.title}*")
        sections.append("")
        sections.append("```")
        sections.append(_extract_template_structure(tmpl.content))
        sections.append("```")
        sections.append("")

    return "\n".join(sections)
