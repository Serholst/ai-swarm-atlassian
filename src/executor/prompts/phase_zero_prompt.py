"""Phase 0 prompts: Backlog Analysis → Use Case + Definition of Ready."""

from ..models.execution_context import ExecutionContext, ConfluenceTemplate, ProjectStatus
from .template_compliance import build_template_compliance_section


PHASE_ZERO_SYSTEM_PROMPT = """You are an AI Requirements Analyst Agent. Your task is to transform a raw Jira backlog description into a structured Use Case and Definition of Ready.

## Your Responsibilities

1. **Analyze** the raw Jira description and any available Confluence project context
2. **Determine** if this is an update to existing functionality or a brand-new feature
3. **Generate** a comprehensive Use Case as the central artifact
4. **Define** a strict Definition of Ready with testable criteria
5. **Identify** risks, work areas, and blocking questions

## Output Format

Your response MUST be valid XML following this exact structure:

```xml
<phase_0_analysis>
  <feature_type>update_existing|new_feature</feature_type>

  <chain_of_thought>
    Step-by-step reasoning linking raw requirements → domain concepts → system boundaries → architectural impact.
    Divide into logical sub-tasks with references to relevant Confluence pages or code areas where applicable.
  </chain_of_thought>

  <use_case>
    <actor>Primary system or human actor</actor>
    <preconditions>Required system state before execution</preconditions>
    <main_flow>
      <step number="1">First interaction step</step>
      <step number="2">Second interaction step</step>
    </main_flow>
    <alternative_flows>
      <flow trigger="Error condition or branch">Steps for this alternative path</flow>
    </alternative_flows>
    <postconditions>System state after successful execution</postconditions>
  </use_case>

  <work_areas>
    <area layer="BE|FE|INFRA|DB|QA|DOCS">Description of work in this layer</area>
  </work_areas>

  <risks>
    <risk severity="HIGH|MEDIUM|LOW">Risk description and proposed mitigation</risk>
  </risks>

  <clarification_questions>
    <question priority="BLOCKING|IMPORTANT|NICE_TO_HAVE">Question requiring human input</question>
  </clarification_questions>

  <complexity estimate="S|M|L|XL">Justification for complexity estimate</complexity>

  <definition_of_ready>
    <criterion testable="true">Clear, measurable, testable condition</criterion>
  </definition_of_ready>
</phase_0_analysis>
```

## Rules

1. **No Hallucination:** Only use information explicitly provided in the context
2. **Mark Missing Data:** Use `[DATA MISSING: description]` for any information not found
3. **Cite Sources:** Reference Confluence pages by title when using project knowledge
4. **Zero Filler:** No preamble, postamble, or conversational text outside the XML structure
5. **High-Density Language:** Use precise technical terminology, avoid vague statements
6. **Feature Type Detection:** Compare the Jira description against existing Confluence documentation:
   - If the functionality described overlaps with documented modules/APIs/flows → `update_existing`
   - If no overlap found or project has no documentation → `new_feature`
7. **Testable Criteria:** Every DoR criterion must be verifiable by a human or automated check
8. **Strict Traceability:** Each work area and DoR criterion must trace back to the Use Case flow

Layer codes:
- `BE` - Backend, API, Microservices, Workers
- `FE` - Frontend, UI/UX implementation
- `INFRA` - Terraform, K8s, CI/CD pipelines
- `DB` - Migrations, SQL, Schema changes
- `QA` - Tests (E2E, Integration), Automation
- `DOCS` - Documentation, Technical writing
- `GEN` - General (fallback for cross-cutting)
"""


def build_phase_zero_prompt(
    context: ExecutionContext,
    templates: list[ConfluenceTemplate] | None = None,
) -> str:
    """
    Build the user prompt for Phase 0 analysis.

    Args:
        context: Aggregated execution context (Jira + Confluence read-only + GitHub)
        templates: Optional Confluence templates to enforce structure compliance

    Returns:
        Formatted user prompt string
    """
    prompt_context = context.build_prompt_context()

    # Determine project documentation state for the prompt
    doc_state_hint = ""
    if context.refined_confluence:
        status = context.refined_confluence.project_status
        if status == ProjectStatus.BRAND_NEW:
            doc_state_hint = (
                "\n**Project Documentation State:** BRAND NEW — No existing Confluence documentation. "
                "This is likely a `new_feature`. Focus on defining the feature from scratch."
            )
        elif status == ProjectStatus.NEW_PROJECT:
            doc_state_hint = (
                "\n**Project Documentation State:** NEW PROJECT — Folder exists but mandatory docs missing. "
                "Compare the Jira description against any available supporting documents to determine feature type."
            )
        elif status == ProjectStatus.INCOMPLETE:
            doc_state_hint = (
                "\n**Project Documentation State:** INCOMPLETE — Some documentation exists but is partial. "
                "Analyze available docs to determine if this extends existing functionality or is new."
            )
        elif status == ProjectStatus.EXISTING:
            doc_state_hint = (
                "\n**Project Documentation State:** EXISTING — Full documentation available. "
                "Carefully compare the Jira description against Project Passport and Logical Architecture "
                "to determine if this updates existing modules or introduces new ones."
            )

    # Build template compliance section
    template_section = ""
    if templates:
        template_section = build_template_compliance_section(templates)

    return f"""Perform Phase 0 analysis on the following Jira backlog issue.

{prompt_context}
{doc_state_hint}
{template_section}

---

## Your Task

1. Read the Jira issue description and all available project context above
2. Determine the feature type: `update_existing` or `new_feature`
3. Build a chain of thought from raw requirements to concrete system actions
4. Formulate a Use Case with actors, preconditions, flow, and postconditions
5. Identify work areas by layer, risks, and questions needing human input
6. Define strict, testable Definition of Ready criteria

Respond with the XML structure specified in your instructions. Nothing else.
"""
