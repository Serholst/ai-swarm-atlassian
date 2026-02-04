"""User prompt builder for LLM execution."""

from ..models.execution_context import ExecutionContext


def build_user_prompt(context: ExecutionContext) -> str:
    """
    Build the user prompt from ExecutionContext.

    Args:
        context: Aggregated execution context (Stage 4 output)

    Returns:
        Formatted user prompt string
    """
    prompt_context = context.build_prompt_context()

    return f"""Analyze the following task and create a detailed work plan.

{prompt_context}

---

## Your Task

Based on the Jira issue and Confluence knowledge base above:

1. Understand what needs to be done
2. Identify any concerns or missing information
3. Analyze the technical approach
4. Create a step-by-step work plan
5. Evaluate the Definition of Ready

Follow the output format specified in your instructions exactly.
If any required information is missing, clearly mark it as `[DATA MISSING: description]`.
"""
