"""Phase 0.5 prompt: Feedback Incorporation after Phase 0 analysis."""

from ..models.execution_context import ExecutionContext, ConfluenceTemplate
from .template_compliance import build_template_compliance_section


PHASE_ZERO_FEEDBACK_SYSTEM_PROMPT = """You are an AI Requirements Analyst Agent performing a feedback incorporation pass (Phase 0.5).

You previously analyzed a Jira backlog issue and raised clarification questions. The task assignee has now provided answers in comments. Your job is to incorporate the feedback and refine the analysis.

## Your Responsibilities

1. **Match** each answer from the assignee to the original clarification question it addresses
2. **Incorporate** new information into your existing analysis — do not start from scratch
3. **Update** the Use Case if answers change scope, constraints, or flows
4. **Re-evaluate** the Definition of Ready with resolved questions
5. **Flag** any NEW questions that arose from the feedback (if any)

## Output Format

Your response MUST be valid XML following the SAME structure as Phase 0:

```xml
<phase_0_analysis>
  <feature_type>update_existing|new_feature</feature_type>

  <chain_of_thought>
    Updated reasoning incorporating the assignee's feedback.
    Reference which questions were answered and how they impact the analysis.
  </chain_of_thought>

  <use_case>
    <actor>Primary system or human actor</actor>
    <preconditions>Updated preconditions with new information</preconditions>
    <main_flow>
      <step number="1">Updated step</step>
    </main_flow>
    <alternative_flows>
      <flow trigger="Condition">Updated alternative path</flow>
    </alternative_flows>
    <postconditions>Updated postconditions</postconditions>
  </use_case>

  <work_areas>
    <area layer="BE|FE|INFRA|DB|QA|DOCS">Updated work area description</area>
  </work_areas>

  <risks>
    <risk severity="HIGH|MEDIUM|LOW">Updated risk (some may be resolved by feedback)</risk>
  </risks>

  <clarification_questions>
    <question priority="RESOLVED" original="Original question text">Summary of the answer received</question>
    <question priority="BLOCKING|IMPORTANT|NICE_TO_HAVE">Any NEW question that arose from feedback</question>
  </clarification_questions>

  <complexity estimate="S|M|L|XL">Updated complexity justification</complexity>

  <definition_of_ready>
    <criterion testable="true">Updated testable condition</criterion>
  </definition_of_ready>
</phase_0_analysis>
```

## Rules

1. **No Hallucination:** Only use information from the original context + previous analysis + assignee feedback
2. **Do NOT discard valid analysis:** Refine the previous pass — do not regenerate from scratch
3. **Resolved questions:** Mark answered questions with `priority="RESOLVED"` and include the answer summary
4. **New questions:** Any new questions must use BLOCKING/IMPORTANT/NICE_TO_HAVE priority
5. **Definition of Ready:** DoR is MET only when ALL BLOCKING questions are resolved
6. **Zero Filler:** No preamble, postamble, or conversational text outside the XML structure
7. **Cite Sources:** Reference which comment answered which question
8. **Strict Traceability:** Updated work areas and DoR criteria must still trace back to Use Case

Layer codes:
- `BE` - Backend, API, Microservices, Workers
- `FE` - Frontend, UI/UX implementation
- `INFRA` - Terraform, K8s, CI/CD pipelines
- `DB` - Migrations, SQL, Schema changes
- `QA` - Tests (E2E, Integration), Automation
- `DOCS` - Documentation, Technical writing
"""


def build_phase_zero_feedback_prompt(
    context: ExecutionContext,
    previous_analysis: str,
    assignee_feedback: list[dict],
    templates: list[ConfluenceTemplate] | None = None,
) -> str:
    """
    Build the user prompt for Phase 0.5 (feedback incorporation).

    Args:
        context: Aggregated execution context (Jira + Confluence + GitHub)
        previous_analysis: The raw XML output from Phase 0
        assignee_feedback: List of assignee comment dicts [{author, created, body}]
        templates: Optional Confluence templates for compliance

    Returns:
        Formatted user prompt string
    """
    prompt_context = context.build_prompt_context()

    # Format assignee feedback
    feedback_lines = []
    for i, fb in enumerate(assignee_feedback, 1):
        feedback_lines.append(
            f"### Comment {i} — {fb['author']} ({fb['created']})\n\n{fb['body']}"
        )
    feedback_text = "\n\n---\n\n".join(feedback_lines)

    # Template compliance section
    template_section = ""
    if templates:
        template_section = build_template_compliance_section(templates)

    return f"""Incorporate assignee feedback into the Phase 0 analysis.

## Original Task Context

{prompt_context}

---

## Previous Phase 0 Analysis (XML)

```xml
{previous_analysis}
```

---

## Assignee Feedback

The task assignee has provided the following answers/comments:

{feedback_text}

---
{template_section}

## Your Task

1. Match each comment to the original clarification questions it addresses
2. Incorporate the new information into ALL relevant sections (Use Case, Work Areas, Risks, DoR)
3. Mark resolved questions with `priority="RESOLVED"` and include the answer summary
4. Re-evaluate the complexity estimate if the feedback changes scope
5. Re-evaluate the Definition of Ready — update criteria as needed
6. Flag any NEW blocking questions that arose from the feedback

Respond with the XML structure specified in your instructions. Nothing else.
"""
