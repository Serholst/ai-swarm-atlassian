"""User prompt builder for LLM execution."""

from ..models.execution_context import ExecutionContext, ProjectStatus


def build_user_prompt(context: ExecutionContext) -> str:
    """
    Build the user prompt from ExecutionContext.

    Args:
        context: Aggregated execution context (Stage 4 output)

    Returns:
        Formatted user prompt string
    """
    prompt_context = context.build_prompt_context()

    # Check if this is a brand-new project
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

    # Add brand-new project specific instructions
    if is_brand_new:
        task_instructions += """

**CRITICAL - NEW PROJECT SETUP:**
This is a brand-new project with no existing Confluence documentation.
Your work plan MUST begin with documentation steps:
- Create Project Passport page (Layer: DOCS)
- Create Logical Architecture page (Layer: DOCS)
These documentation steps MUST be completed BEFORE any implementation steps."""

    return f"""Analyze the following task and create a detailed work plan.

{prompt_context}

---

## Your Task

{task_instructions}

Follow the output format specified in your instructions exactly.
If any required information is missing, clearly mark it as `[DATA MISSING: description]`.
"""
