"""User prompt builder for LLM execution."""

from ..models.execution_context import ExecutionContext, ProjectStatus
from .template_compliance import build_template_compliance_section


def build_user_prompt(context: ExecutionContext) -> str:
    """
    Build the user prompt from ExecutionContext.

    Args:
        context: Aggregated execution context (Stage 4 output)

    Returns:
        Formatted user prompt string
    """
    prompt_context = context.build_prompt_context()

    # Check if this project needs documentation setup
    needs_doc_setup = (
        context.refined_confluence
        and context.refined_confluence.project_status in (
            ProjectStatus.BRAND_NEW,
            ProjectStatus.NEW_PROJECT,
            ProjectStatus.INCOMPLETE,
        )
    )
    is_brand_new = (
        context.refined_confluence
        and context.refined_confluence.project_status == ProjectStatus.BRAND_NEW
    )

    # Determine data sources available
    data_sources = ["Jira issue"]
    if context.refined_confluence or context.confluence:
        # Don't add Confluence as data source for brand-new projects (no docs exist)
        if not is_brand_new:
            data_sources.append("Confluence knowledge base")
    if context.github and context.github.is_available():
        data_sources.append("GitHub codebase")

    sources_text = ", ".join(data_sources)

    # Build task instructions
    task_instructions = f"""Based on the {sources_text} above:

1. Understand what needs to be done
2. Identify any concerns or missing information
3. Analyze the technical approach (considering existing codebase patterns if available)
4. Create a step-by-step work plan aligned with existing architecture
5. Evaluate the Definition of Ready"""

    # Add documentation setup instructions for projects with gaps
    if needs_doc_setup:
        missing = context.refined_confluence.missing_critical_data or []
        missing_text = "\n".join(f"- {item}" for item in missing) if missing else "- Project Passport\n- Logical Architecture"

        task_instructions += f"""

**CRITICAL - PROJECT DOCUMENTATION SETUP:**
This project has documentation gaps that must be addressed.

Documentation gaps:
{missing_text}

Your work plan MUST include steps to create or fill these pages (Layer: DOCS).
These documentation steps MUST be completed BEFORE any implementation steps."""

    # Add template compliance instructions if templates are available
    if context.confluence_templates:
        task_instructions += build_template_compliance_section(context.confluence_templates)

    return f"""Analyze the following task and create a detailed work plan.

{prompt_context}

---

## Your Task

{task_instructions}

Follow the output format specified in your instructions exactly.
If any required information is missing, clearly mark it as `[DATA MISSING: description]`.
"""


def _build_refinement_context(context: ExecutionContext) -> str:
    """Build a lightweight context summary for refinement prompts.

    The previous plan already contains the LLM's understanding of the full context,
    so we only need Jira metadata to anchor the refinement — not the full Confluence
    docs and GitHub context again.
    """
    lines = [
        f"**Key:** {context.issue_key}",
    ]
    if context.jira:
        lines.append(f"**Title:** {context.jira.summary}")
        lines.append(f"**Type:** {context.jira.issue_type}")
        lines.append(f"**Status:** {context.jira.status}")
        lines.append(f"**Project:** {context.jira.project_name} ({context.jira.project_key})")
        if context.jira.components:
            lines.append(f"**Components:** {', '.join(context.jira.components)}")
    return "\n".join(lines)


def build_refinement_prompt(
    context: ExecutionContext,
    feedback: str,
    previous_plan: str,
    version: int = 2,
) -> str:
    """
    Build a refinement prompt that incorporates human feedback on a previous plan.

    Uses a lightweight context summary (Jira metadata only) instead of the full
    context, since the previous plan already encapsulates the LLM's analysis.

    Args:
        context: Original ExecutionContext (loaded from context store)
        feedback: Human feedback describing changes to make
        previous_plan: The previous work plan text
        version: Refinement version number (2, 3, ...)

    Returns:
        Formatted refinement prompt string
    """
    refinement_context = _build_refinement_context(context)

    return f"""You are refining an existing work plan (version {version}) based on human feedback.

## Task Reference

{refinement_context}

---

## Previous Work Plan (v{version - 1})

{previous_plan}

---

## Human Feedback

{feedback}

---

## Your Task

Incorporate the human feedback into the work plan. Specifically:

1. **Review** the previous work plan carefully
2. **Apply** the requested changes from human feedback
3. **Regenerate** a complete, updated work plan

**Important:**
- Keep all sections from the original format (Understanding, Concerns, Analysis, Work Plan, Definition of Ready)
- Only modify sections affected by the feedback — preserve unchanged parts
- In the Work Plan, maintain proper step numbering, Layers, Files, Acceptance, and Depends on fields
- If the feedback requests adding/removing/splitting steps, update all step numbers and dependencies accordingly
- Mark this as "Refined Plan v{version}" in your Understanding section

Follow the output format specified in your instructions exactly.
"""
