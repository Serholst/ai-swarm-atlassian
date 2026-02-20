"""
Phase 0: Backlog Analysis — Use Case + Definition of Ready.

Transforms raw Jira backlog descriptions into structured artifacts:
- Chain of Thought (requirements → system actions)
- Use Case (actors, preconditions, flow, postconditions)
- Work Areas by layer
- Risks with severity
- Clarification Questions
- Complexity estimate
- Definition of Ready (testable criteria)

Constraints:
- Output goes to Jira ONLY (comment on the issue)
- Confluence is READ-ONLY (context gathering)
- No Confluence writes while issue is in Backlog
"""

import re
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

from openai import OpenAI

from ..mcp.client import MCPClientManager
from ..models.execution_context import ExecutionContext
from ..models.llm_metrics import LLMCallMetrics, ExecutionMetrics
from ..prompts.phase_zero_prompt import PHASE_ZERO_SYSTEM_PROMPT, build_phase_zero_prompt

logger = logging.getLogger(__name__)


# =============================================================================
# Data Models
# =============================================================================

@dataclass
class PhaseZeroResponse:
    """Parsed Phase 0 LLM response."""

    raw_content: str

    # Parsed XML sections
    feature_type: str = ""  # "update_existing" | "new_feature"
    chain_of_thought: str = ""
    use_case: str = ""
    work_areas: str = ""
    risks: str = ""
    clarification_questions: str = ""
    complexity: str = ""
    complexity_estimate: str = ""  # S|M|L|XL
    definition_of_ready: str = ""

    # LLM metadata
    model: str = ""
    tokens_used: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    finish_reason: str = ""


@dataclass
class PhaseZeroResult:
    """Result of Phase 0 execution."""

    response: PhaseZeroResponse
    jira_updated: bool
    output_file: Optional[Path] = None
    dor_met: bool = False
    validation_errors: list[str] = field(default_factory=list)
    error: Optional[str] = None


# =============================================================================
# XML Parsing
# =============================================================================

def _extract_xml_tag(content: str, tag: str) -> str:
    """Extract content between XML tags. Returns empty string if not found."""
    pattern = rf"<{tag}[^>]*>(.*?)</{tag}>"
    match = re.search(pattern, content, re.DOTALL)
    return match.group(1).strip() if match else ""


def _extract_xml_tag_with_attr(content: str, tag: str, attr_name: str) -> tuple[str, str]:
    """Extract content and a specific attribute from an XML tag."""
    pattern = rf'<{tag}\s+{attr_name}="([^"]*)"[^>]*>(.*?)</{tag}>'
    match = re.search(pattern, content, re.DOTALL)
    if match:
        return match.group(1), match.group(2).strip()
    return "", _extract_xml_tag(content, tag)


def parse_phase_zero_response(raw: str) -> PhaseZeroResponse:
    """
    Parse Phase 0 XML response from LLM output.

    Args:
        raw: Raw LLM response text

    Returns:
        PhaseZeroResponse with parsed sections
    """
    response = PhaseZeroResponse(raw_content=raw)

    # Extract top-level sections
    response.feature_type = _extract_xml_tag(raw, "feature_type")
    response.chain_of_thought = _extract_xml_tag(raw, "chain_of_thought")
    response.use_case = _extract_xml_tag(raw, "use_case")
    response.work_areas = _extract_xml_tag(raw, "work_areas")
    response.risks = _extract_xml_tag(raw, "risks")
    response.clarification_questions = _extract_xml_tag(raw, "clarification_questions")
    response.definition_of_ready = _extract_xml_tag(raw, "definition_of_ready")

    # Complexity has an attribute
    estimate, justification = _extract_xml_tag_with_attr(raw, "complexity", "estimate")
    response.complexity_estimate = estimate
    response.complexity = justification

    return response


# =============================================================================
# Validation
# =============================================================================

def validate_phase_zero(response: PhaseZeroResponse) -> list[str]:
    """
    Validate Phase 0 response has all required sections.

    Args:
        response: Parsed Phase 0 response

    Returns:
        List of validation error messages (empty = valid)
    """
    errors = []

    if not response.chain_of_thought:
        errors.append("Missing <chain_of_thought> section")
    if not response.use_case:
        errors.append("Missing <use_case> section")
    if not response.definition_of_ready:
        errors.append("Missing <definition_of_ready> section")
    if not response.feature_type:
        errors.append("Missing <feature_type> (expected 'update_existing' or 'new_feature')")
    elif response.feature_type not in ("update_existing", "new_feature"):
        errors.append(f"Invalid <feature_type>: '{response.feature_type}' (expected 'update_existing' or 'new_feature')")
    if not response.complexity_estimate:
        errors.append("Missing complexity estimate attribute (expected S|M|L|XL)")
    elif response.complexity_estimate not in ("S", "M", "L", "XL"):
        errors.append(f"Invalid complexity estimate: '{response.complexity_estimate}' (expected S|M|L|XL)")

    # Check use_case has required sub-elements
    if response.use_case:
        for sub_tag in ("actor", "preconditions", "main_flow", "postconditions"):
            if not _extract_xml_tag(response.use_case, sub_tag):
                errors.append(f"Missing <{sub_tag}> in <use_case>")

    # Check definition_of_ready has at least one criterion
    if response.definition_of_ready:
        criteria = re.findall(r"<criterion[^>]*>", response.definition_of_ready)
        if not criteria:
            errors.append("No <criterion> elements found in <definition_of_ready>")

    return errors


# =============================================================================
# Jira Description Builder (ADF with Expand blocks)
# =============================================================================

class _ADF:
    """Minimal ADF builder for Phase 0 description output.

    Builds Atlassian Document Format nodes without importing jira_server
    (which requires environment variables for its MCP server initialization).
    """

    @staticmethod
    def heading(text: str, level: int) -> dict:
        return {
            "type": "heading",
            "attrs": {"level": level},
            "content": [{"type": "text", "text": text}],
        }

    @staticmethod
    def paragraph(text: str) -> dict:
        return {
            "type": "paragraph",
            "content": _ADF.parse_inline(text),
        }

    @staticmethod
    def parse_inline(text: str) -> list[dict]:
        """Parse bold (**text**) and plain text."""
        result = []
        remaining = text
        while remaining:
            bold = re.match(r"\*\*([^*]+)\*\*", remaining)
            if bold:
                result.append({
                    "type": "text",
                    "text": bold.group(1),
                    "marks": [{"type": "strong"}],
                })
                remaining = remaining[bold.end():]
                continue
            italic = re.match(r"\*([^*]+)\*", remaining)
            if italic:
                result.append({
                    "type": "text",
                    "text": italic.group(1),
                    "marks": [{"type": "em"}],
                })
                remaining = remaining[italic.end():]
                continue
            plain = re.match(r"[^*]+", remaining)
            if plain:
                result.append({"type": "text", "text": plain.group()})
                remaining = remaining[plain.end():]
                continue
            result.append({"type": "text", "text": remaining[0]})
            remaining = remaining[1:]
        return result or [{"type": "text", "text": text}]

    @staticmethod
    def bullet_list(items: list[str]) -> dict:
        return {
            "type": "bulletList",
            "content": [
                {"type": "listItem", "content": [_ADF.paragraph(item)]}
                for item in items
            ],
        }

    @staticmethod
    def ordered_list(items: list[str]) -> dict:
        return {
            "type": "orderedList",
            "content": [
                {"type": "listItem", "content": [_ADF.paragraph(item)]}
                for item in items
            ],
        }

    @staticmethod
    def rule() -> dict:
        return {"type": "rule"}

    @staticmethod
    def expand_block(title: str, body_nodes: list[dict]) -> dict:
        """Create an ADF expand (collapsible) block from pre-built nodes."""
        return {
            "type": "expand",
            "attrs": {"title": title},
            "content": body_nodes,
        }

    @staticmethod
    def markdown_to_nodes(md: str) -> list[dict]:
        """Convert simple markdown to ADF nodes."""
        nodes = []
        lines = md.split("\n")
        i = 0
        while i < len(lines):
            line = lines[i]

            # Header
            hdr = re.match(r"^(#{1,6})\s+(.+)$", line)
            if hdr:
                nodes.append(_ADF.heading(hdr.group(2), len(hdr.group(1))))
                i += 1
                continue

            # Bullet list
            if re.match(r"^[-*]\s+", line):
                items = []
                while i < len(lines) and re.match(r"^[-*]\s+", lines[i]):
                    items.append(re.sub(r"^[-*]\s+", "", lines[i]))
                    i += 1
                nodes.append(_ADF.bullet_list(items))
                continue

            # Numbered list
            if re.match(r"^\d+\.\s+", line):
                items = []
                while i < len(lines) and re.match(r"^\d+\.\s+", lines[i]):
                    items.append(re.sub(r"^\d+\.\s+", "", lines[i]))
                    i += 1
                nodes.append(_ADF.ordered_list(items))
                continue

            # Empty line
            if not line.strip():
                i += 1
                continue

            # Paragraph (collect consecutive non-special lines)
            para_lines = [line]
            i += 1
            while i < len(lines) and lines[i].strip() and not re.match(r"^(#{1,6}\s|[-*]\s|\d+\.\s)", lines[i]):
                para_lines.append(lines[i])
                i += 1
            nodes.append(_ADF.paragraph(" ".join(para_lines)))

        return nodes


def _format_use_case_markdown(use_case_xml: str) -> str:
    """Convert use case XML to readable Markdown."""
    actor = _extract_xml_tag(use_case_xml, "actor")
    preconditions = _extract_xml_tag(use_case_xml, "preconditions")
    postconditions = _extract_xml_tag(use_case_xml, "postconditions")

    main_flow = _extract_xml_tag(use_case_xml, "main_flow")
    steps = re.findall(r'<step\s+number="(\d+)"[^>]*>(.*?)</step>', main_flow, re.DOTALL)

    alt_flows_xml = _extract_xml_tag(use_case_xml, "alternative_flows")
    alt_flows = re.findall(r'<flow\s+trigger="([^"]*)"[^>]*>(.*?)</flow>', alt_flows_xml, re.DOTALL)

    lines = [
        f"**Actor:** {actor}",
        f"**Preconditions:** {preconditions}",
        "",
        "**Main Flow:**",
    ]
    for num, desc in steps:
        lines.append(f"{num}. {desc.strip()}")

    if alt_flows:
        lines.append("")
        lines.append("**Alternative Flows:**")
        for trigger, flow_desc in alt_flows:
            lines.append(f"- *{trigger}:* {flow_desc.strip()}")

    lines.append("")
    lines.append(f"**Postconditions:** {postconditions}")
    return "\n".join(lines)


def _format_work_areas_markdown(work_areas_xml: str) -> str:
    """Convert work areas XML to Markdown."""
    areas = re.findall(r'<area\s+layer="([^"]*)"[^>]*>(.*?)</area>', work_areas_xml, re.DOTALL)
    if not areas:
        return "_No work areas identified_"
    return "\n".join(f"- **[{layer}]** {desc.strip()}" for layer, desc in areas)


def _format_risks_markdown(risks_xml: str) -> str:
    """Convert risks XML to Markdown."""
    risks = re.findall(r'<risk\s+severity="([^"]*)"[^>]*>(.*?)</risk>', risks_xml, re.DOTALL)
    if not risks:
        return "_No risks identified_"
    return "\n".join(f"- **[{sev}]** {desc.strip()}" for sev, desc in risks)


def _format_questions_markdown(questions_xml: str) -> str:
    """Convert clarification questions XML to Markdown."""
    questions = re.findall(
        r'<question\s+priority="([^"]*)"[^>]*>(.*?)</question>',
        questions_xml, re.DOTALL,
    )
    if not questions:
        return ""
    return "\n".join(f"- **[{pri}]** {q.strip()}" for pri, q in questions)


def _format_dor_markdown(dor_xml: str) -> str:
    """Convert definition of ready XML to Markdown checklist."""
    criteria = re.findall(r"<criterion[^>]*>(.*?)</criterion>", dor_xml, re.DOTALL)
    if not criteria:
        return "_No criteria defined_"
    return "\n".join(f"- [ ] {c.strip()}" for c in criteria)


def build_jira_description_adf(
    response: PhaseZeroResponse,
    issue_key: str,
    original_description: str = "",
) -> dict:
    """
    Build an ADF document for the Jira issue description.

    Layout:
    - Questions / Clarifications (visible at top, NOT in Expand)
    - Expand: Original Requirements
    - Expand: Chain of Thought
    - Expand: Use Cases
    - Expand: Definition of Ready
    - Expand: Complexity Justification

    Args:
        response: Parsed Phase 0 response
        issue_key: Jira issue key
        original_description: The original issue description text to preserve

    Returns:
        ADF document dict ready for Jira API
    """
    feature_label = "New Feature" if response.feature_type == "new_feature" else "Update Existing"
    content = []

    # --- Header ---
    content.append(_ADF.heading("Phase 0: Requirements Analysis", 2))
    content.append(_ADF.paragraph(
        f"**Feature Type:** {feature_label}  |  **Complexity:** {response.complexity_estimate}"
    ))

    # --- Divider ---
    content.append(_ADF.rule())

    # --- Questions / Clarifications (VISIBLE — NOT in Expand) ---
    questions_md = _format_questions_markdown(response.clarification_questions) if response.clarification_questions else ""
    if questions_md:
        content.append(_ADF.heading("Questions / Clarifications Needed", 3))
        content.extend(_ADF.markdown_to_nodes(questions_md))
        content.append(_ADF.rule())

    # --- Expand: Original Requirements ---
    if original_description:
        content.append(_ADF.expand_block(
            "Original Requirements",
            _ADF.markdown_to_nodes(original_description),
        ))

    # --- Expand: Chain of Thought ---
    cot_md = response.chain_of_thought or "_No chain of thought generated_"
    content.append(_ADF.expand_block("Chain of Thought", _ADF.markdown_to_nodes(cot_md)))

    # --- Expand: Use Cases ---
    use_case_md = _format_use_case_markdown(response.use_case) if response.use_case else "_Use case not generated_"
    work_areas_md = _format_work_areas_markdown(response.work_areas) if response.work_areas else ""
    risks_md = _format_risks_markdown(response.risks) if response.risks else ""

    full_use_case_md = use_case_md
    if work_areas_md:
        full_use_case_md += f"\n\n### Work Areas\n\n{work_areas_md}"
    if risks_md:
        full_use_case_md += f"\n\n### Risks\n\n{risks_md}"

    content.append(_ADF.expand_block("Use Cases", _ADF.markdown_to_nodes(full_use_case_md)))

    # --- Expand: Definition of Ready ---
    dor_md = _format_dor_markdown(response.definition_of_ready) if response.definition_of_ready else "_No criteria_"
    content.append(_ADF.expand_block("Definition of Ready", _ADF.markdown_to_nodes(dor_md)))

    # --- Expand: Complexity Justification ---
    if response.complexity:
        content.append(_ADF.expand_block(
            "Complexity Justification",
            _ADF.markdown_to_nodes(response.complexity),
        ))

    # --- Footer ---
    content.append(_ADF.rule())
    content.append(_ADF.paragraph(
        f"*Generated by AI-SWARM Phase 0 | {issue_key} | Ready for human review*"
    ))

    return {"type": "doc", "version": 1, "content": content}


# =============================================================================
# Output File
# =============================================================================

def save_phase_zero_output(
    issue_key: str,
    response: PhaseZeroResponse,
    context: ExecutionContext,
    validation_errors: list[str],
    output_dir: str = "outputs",
) -> Path:
    """Save Phase 0 output to markdown file."""
    issue_dir = Path(output_dir) / issue_key
    issue_dir.mkdir(parents=True, exist_ok=True)

    filepath = issue_dir / f"{issue_key}_phase0.md"

    summary = context.jira.summary if context.jira else issue_key

    content = f"""# Phase 0 Analysis: {issue_key}

**Task:** {summary}
**Generated:** {datetime.now().isoformat()}
**Model:** {response.model}
**Tokens Used:** {response.tokens_used}
**Feature Type:** {response.feature_type}
**Complexity:** {response.complexity_estimate}

---

## Validation

{"**PASSED** — All required sections present." if not validation_errors else "**FAILED** — Errors:"}
"""
    if validation_errors:
        for err in validation_errors:
            content += f"\n- {err}"
        content += "\n"

    content += f"""
---

## Raw LLM Response

```xml
{response.raw_content}
```
"""
    filepath.write_text(content, encoding="utf-8")
    return filepath


def _load_previous_phase0_xml(output_file: Path) -> str:
    """
    Load the raw XML from a previous Phase 0 output file.

    The Phase 0 output file contains the raw LLM response in a fenced XML block:
    ```xml
    <phase_0_analysis>...</phase_0_analysis>
    ```

    Args:
        output_file: Path to the Phase 0 output markdown file

    Returns:
        Raw XML string, or empty string if not found
    """
    if not output_file.exists():
        return ""

    try:
        content = output_file.read_text(encoding="utf-8")
        # Extract XML from fenced code block
        match = re.search(r"```xml\s*\n(.*?)\n```", content, re.DOTALL)
        if match:
            return match.group(1).strip()
    except Exception as e:
        logger.warning(f"Failed to load previous Phase 0 output: {e}")

    return ""


# =============================================================================
# Phase 0 Executor
# =============================================================================

def execute_phase_zero(
    mcp: MCPClientManager,
    llm_client: OpenAI,
    issue_key: str,
    config: dict,
    output_dir: str = "outputs",
    model: str = "deepseek-chat",
    dry_run: bool = False,
) -> PhaseZeroResult:
    """
    Execute Phase 0: Backlog Analysis pipeline.

    1. Reuse Stages 2-4 for context building (Jira + Confluence read-only + GitHub)
    2. Build Phase 0 prompt
    3. Call LLM (DeepSeek)
    4. Parse and validate XML response
    5. Save output file
    6. Add Jira comment with structured results

    Args:
        mcp: MCP client manager (started)
        llm_client: OpenAI client configured for DeepSeek
        issue_key: Jira issue key
        config: SDLC config dict
        output_dir: Output directory
        model: LLM model name
        dry_run: If True, skip Jira comment

    Returns:
        PhaseZeroResult with analysis results
    """
    from .context_builder import build_refined_context_pipeline

    logger.info(f"Phase 0: Starting backlog analysis for {issue_key}")

    # --- Step 1: Build context (reuse Stages 2-4) ---
    logger.info("Phase 0: Building context (Stages 2-4)")
    try:
        execution_context = build_refined_context_pipeline(
            mcp=mcp,
            llm_client=llm_client,
            task_input=issue_key,
            config=config,
        )
    except Exception as e:
        logger.error(f"Phase 0: Context building failed: {e}")
        return PhaseZeroResult(
            response=PhaseZeroResponse(raw_content=""),
            jira_updated=False,
            error=f"Context building failed: {e}",
        )

    if not execution_context.is_valid():
        logger.error(f"Phase 0: Context validation failed: {execution_context.errors}")
        return PhaseZeroResult(
            response=PhaseZeroResponse(raw_content=""),
            jira_updated=False,
            error=f"Context validation failed: {execution_context.errors}",
        )

    # --- Step 1a: Auto-detect Phase 0.5 (feedback incorporation) ---
    from .context_builder import has_existing_phase0_analysis, extract_assignee_feedback

    if has_existing_phase0_analysis(execution_context):
        logger.info("Phase 0: Existing Phase 0 analysis detected — checking for assignee feedback")

        assignee_id = (
            execution_context.jira.assignee_account_id
            if execution_context.jira else None
        )

        # Load previous analysis — prefer Phase 0.5 output (latest), fall back to Phase 0
        issue_dir = Path(output_dir) / issue_key
        previous_output_file = issue_dir / f"{issue_key}_phase05.md"
        if not previous_output_file.exists():
            previous_output_file = issue_dir / f"{issue_key}_phase0.md"
        previous_analysis = _load_previous_phase0_xml(previous_output_file)

        if assignee_id and previous_analysis:
            # Get the Phase 0 output file's modification time as the timestamp gate
            phase0_timestamp = None
            if previous_output_file.exists():
                from datetime import timezone
                mtime = previous_output_file.stat().st_mtime
                phase0_timestamp = datetime.fromtimestamp(
                    mtime, tz=timezone.utc
                ).strftime("%Y-%m-%dT%H:%M:%S")

            assignee_feedback = extract_assignee_feedback(
                mcp=mcp,
                issue_key=issue_key,
                assignee_account_id=assignee_id,
                phase0_timestamp=phase0_timestamp,
            )

            if assignee_feedback:
                logger.info(
                    f"Phase 0: Routing to Phase 0.5 — "
                    f"{len(assignee_feedback)} assignee comment(s) found"
                )
                return execute_phase_zero_feedback(
                    mcp=mcp,
                    llm_client=llm_client,
                    issue_key=issue_key,
                    execution_context=execution_context,
                    previous_analysis=previous_analysis,
                    assignee_feedback=assignee_feedback,
                    config=config,
                    output_dir=output_dir,
                    model=model,
                    dry_run=dry_run,
                )
            else:
                logger.info(
                    "Phase 0: No assignee feedback found — re-running Phase 0 from scratch"
                )
        else:
            if not assignee_id:
                logger.info("Phase 0: No assignee account ID — cannot check for feedback")
            if not previous_analysis:
                logger.info("Phase 0: No previous Phase 0 output found — running fresh")

    # --- Step 1b: Retrieve Confluence templates (Template Compliance) ---
    from .context_builder import retrieve_confluence_templates

    templates = []
    space_key = execution_context.jira.project_key if execution_context.jira else ""
    hub_space = (config or {}).get("confluence", {}).get("templates_space")
    if space_key:
        try:
            templates = retrieve_confluence_templates(mcp, space_key, hub_space=hub_space)
            execution_context.confluence_templates = templates
            if templates:
                logger.info(f"Phase 0: Retrieved {len(templates)} templates for compliance")
            else:
                logger.info("Phase 0: No templates found — using default layout")
        except Exception as e:
            logger.warning(f"Phase 0: Template retrieval failed (non-fatal): {e}")

    # --- Step 2: Build Phase 0 prompt ---
    user_prompt = build_phase_zero_prompt(execution_context, templates=templates)
    logger.info(f"Phase 0: Prompt built ({len(user_prompt)} chars)")

    # Save prompt for audit
    issue_dir = Path(output_dir) / issue_key
    issue_dir.mkdir(parents=True, exist_ok=True)
    prompt_file = issue_dir / f"{issue_key}_phase0_prompt.md"
    prompt_file.write_text(
        f"# Phase 0 Prompt: {issue_key}\n\n"
        f"Generated: {datetime.now().isoformat()}\n"
        f"Model: {model}\n\n---\n\n"
        f"## System Prompt\n\n```\n{PHASE_ZERO_SYSTEM_PROMPT}\n```\n\n---\n\n"
        f"## User Prompt\n\n{user_prompt}\n",
        encoding="utf-8",
    )

    # --- Step 3: Call LLM ---
    logger.info(f"Phase 0: Calling LLM ({model})")
    start_time = time.time()

    try:
        completion = llm_client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": PHASE_ZERO_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.2,
            max_tokens=8192,
        )

        raw_content = completion.choices[0].message.content or ""
        tokens_in = completion.usage.prompt_tokens if completion.usage else 0
        tokens_out = completion.usage.completion_tokens if completion.usage else 0
        tokens_used = tokens_in + tokens_out
        finish_reason = completion.choices[0].finish_reason or ""

    except Exception as e:
        logger.error(f"Phase 0: LLM call failed: {e}")
        return PhaseZeroResult(
            response=PhaseZeroResponse(raw_content=""),
            jira_updated=False,
            error=f"LLM call failed: {e}",
        )

    duration_ms = int((time.time() - start_time) * 1000)
    logger.info(f"Phase 0: LLM response received ({tokens_used} tokens, {duration_ms}ms)")

    # --- Step 4: Parse and validate ---
    response = parse_phase_zero_response(raw_content)
    response.model = model
    response.tokens_used = tokens_used
    response.tokens_in = tokens_in
    response.tokens_out = tokens_out
    response.finish_reason = finish_reason

    validation_errors = validate_phase_zero(response)
    if validation_errors:
        logger.warning(f"Phase 0: Validation errors: {validation_errors}")
    else:
        logger.info("Phase 0: Validation passed")

    # --- Step 5: Save output file ---
    output_file = save_phase_zero_output(
        issue_key=issue_key,
        response=response,
        context=execution_context,
        validation_errors=validation_errors,
        output_dir=output_dir,
    )
    logger.info(f"Phase 0: Output saved: {output_file}")

    # --- Step 6: Update Jira issue description ---
    jira_updated = False
    if not dry_run:
        # Preserve original description
        original_desc = execution_context.jira.description if execution_context.jira else ""

        adf_doc = build_jira_description_adf(response, issue_key, original_desc)
        try:
            mcp.jira_update_description(issue_key, adf_doc)
            jira_updated = True
            logger.info(f"Phase 0: Jira description updated for {issue_key}")
        except Exception as e:
            logger.error(f"Phase 0: Failed to update Jira description: {e}")

    # Determine if DoR is met (no validation errors + no BLOCKING questions)
    has_blocking = bool(
        response.clarification_questions
        and re.search(r'priority="BLOCKING"', response.clarification_questions)
    )
    dor_met = not validation_errors and not has_blocking

    return PhaseZeroResult(
        response=response,
        jira_updated=jira_updated,
        output_file=output_file,
        dor_met=dor_met,
        validation_errors=validation_errors,
    )


# =============================================================================
# Phase 0.5: Feedback Incorporation
# =============================================================================

def execute_phase_zero_feedback(
    mcp: MCPClientManager,
    llm_client: OpenAI,
    issue_key: str,
    execution_context: ExecutionContext,
    previous_analysis: str,
    assignee_feedback: list[dict],
    config: dict,
    output_dir: str = "outputs",
    model: str = "deepseek-chat",
    dry_run: bool = False,
) -> PhaseZeroResult:
    """
    Execute Phase 0.5: Incorporate assignee feedback into Phase 0 analysis.

    Sends the original analysis + assignee comments to the LLM for refinement.
    Updates the Jira description with the refined analysis.
    Evaluates DoR: if all BLOCKING questions are RESOLVED → DoR met.

    Args:
        mcp: MCP client manager (started)
        llm_client: OpenAI client configured for DeepSeek
        issue_key: Jira issue key
        execution_context: Pre-built execution context (from Phase 0 re-run of Stages 2-4)
        previous_analysis: Raw XML from the previous Phase 0 LLM response
        assignee_feedback: Filtered assignee comments [{author, account_id, created, body}]
        config: SDLC config dict
        output_dir: Output directory
        model: LLM model name
        dry_run: If True, skip Jira update

    Returns:
        PhaseZeroResult with refined analysis
    """
    from ..prompts.phase_zero_feedback_prompt import (
        PHASE_ZERO_FEEDBACK_SYSTEM_PROMPT,
        build_phase_zero_feedback_prompt,
    )

    logger.info(f"Phase 0.5: Starting feedback incorporation for {issue_key}")
    logger.info(f"Phase 0.5: {len(assignee_feedback)} assignee comment(s) to process")

    # --- Step 1: Retrieve templates (Template Compliance) ---
    from .context_builder import retrieve_confluence_templates

    templates = []
    space_key = execution_context.jira.project_key if execution_context.jira else ""
    hub_space = (config or {}).get("confluence", {}).get("templates_space")
    if space_key:
        try:
            templates = retrieve_confluence_templates(mcp, space_key, hub_space=hub_space)
            execution_context.confluence_templates = templates
        except Exception as e:
            logger.warning(f"Phase 0.5: Template retrieval failed (non-fatal): {e}")

    # --- Step 2: Build Phase 0.5 prompt ---
    user_prompt = build_phase_zero_feedback_prompt(
        context=execution_context,
        previous_analysis=previous_analysis,
        assignee_feedback=assignee_feedback,
        templates=templates if templates else None,
    )
    logger.info(f"Phase 0.5: Prompt built ({len(user_prompt)} chars)")

    # Save prompt for audit
    issue_dir = Path(output_dir) / issue_key
    issue_dir.mkdir(parents=True, exist_ok=True)
    prompt_file = issue_dir / f"{issue_key}_phase05_prompt.md"
    prompt_file.write_text(
        f"# Phase 0.5 Prompt: {issue_key}\n\n"
        f"Generated: {datetime.now().isoformat()}\n"
        f"Model: {model}\n"
        f"Feedback comments: {len(assignee_feedback)}\n\n---\n\n"
        f"## System Prompt\n\n```\n{PHASE_ZERO_FEEDBACK_SYSTEM_PROMPT}\n```\n\n---\n\n"
        f"## User Prompt\n\n{user_prompt}\n",
        encoding="utf-8",
    )

    # --- Step 3: Call LLM ---
    logger.info(f"Phase 0.5: Calling LLM ({model})")
    start_time = time.time()

    try:
        completion = llm_client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": PHASE_ZERO_FEEDBACK_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.2,
            max_tokens=8192,
        )

        raw_content = completion.choices[0].message.content or ""
        tokens_in = completion.usage.prompt_tokens if completion.usage else 0
        tokens_out = completion.usage.completion_tokens if completion.usage else 0
        tokens_used = tokens_in + tokens_out
        finish_reason = completion.choices[0].finish_reason or ""

    except Exception as e:
        logger.error(f"Phase 0.5: LLM call failed: {e}")
        return PhaseZeroResult(
            response=PhaseZeroResponse(raw_content=""),
            jira_updated=False,
            error=f"LLM call failed: {e}",
        )

    duration_ms = int((time.time() - start_time) * 1000)
    logger.info(f"Phase 0.5: LLM response received ({tokens_used} tokens, {duration_ms}ms)")

    # --- Step 4: Parse and validate ---
    response = parse_phase_zero_response(raw_content)
    response.model = model
    response.tokens_used = tokens_used
    response.tokens_in = tokens_in
    response.tokens_out = tokens_out
    response.finish_reason = finish_reason

    validation_errors = validate_phase_zero(response)
    if validation_errors:
        logger.warning(f"Phase 0.5: Validation errors: {validation_errors}")
    else:
        logger.info("Phase 0.5: Validation passed")

    # --- Step 5: Save output file ---
    output_file = _save_phase05_output(
        issue_key=issue_key,
        response=response,
        context=execution_context,
        assignee_feedback=assignee_feedback,
        validation_errors=validation_errors,
        output_dir=output_dir,
    )
    logger.info(f"Phase 0.5: Output saved: {output_file}")

    # --- Step 6: Update Jira description ---
    jira_updated = False
    if not dry_run:
        # Use the original description (before Phase 0 modified it)
        # We reconstruct from the "Original Requirements" expand block
        original_desc = _extract_original_description(execution_context)

        adf_doc = build_jira_description_adf(response, issue_key, original_desc)
        try:
            mcp.jira_update_description(issue_key, adf_doc)
            jira_updated = True
            logger.info(f"Phase 0.5: Jira description updated for {issue_key}")
        except Exception as e:
            logger.error(f"Phase 0.5: Failed to update Jira description: {e}")

    # --- Step 7: Determine DoR status ---
    # DoR met = no validation errors + no unresolved BLOCKING questions
    # RESOLVED questions have priority="RESOLVED", not "BLOCKING"
    has_unresolved_blocking = bool(
        response.clarification_questions
        and re.search(r'priority="BLOCKING"', response.clarification_questions)
    )
    dor_met = not validation_errors and not has_unresolved_blocking

    if dor_met:
        logger.info("Phase 0.5: DoR MET — all blocking questions resolved")
        # Transition to AI To Do if not dry-run
        if not dry_run:
            try:
                mcp.jira_transition_issue(issue_key, "AI To Do")
                logger.info(f"Phase 0.5: Transitioned {issue_key} to 'AI To Do'")
            except Exception as e:
                logger.warning(f"Phase 0.5: Transition to 'AI To Do' failed: {e}")
    else:
        logger.info("Phase 0.5: DoR NOT MET — unresolved blocking questions remain")

    return PhaseZeroResult(
        response=response,
        jira_updated=jira_updated,
        output_file=output_file,
        dor_met=dor_met,
        validation_errors=validation_errors,
    )


def _save_phase05_output(
    issue_key: str,
    response: PhaseZeroResponse,
    context: ExecutionContext,
    assignee_feedback: list[dict],
    validation_errors: list[str],
    output_dir: str = "outputs",
) -> Path:
    """Save Phase 0.5 output to markdown file."""
    issue_dir = Path(output_dir) / issue_key
    issue_dir.mkdir(parents=True, exist_ok=True)

    filepath = issue_dir / f"{issue_key}_phase05.md"

    summary = context.jira.summary if context.jira else issue_key

    feedback_summary = "\n".join(
        f"- **{fb['author']}** ({fb['created']}): {fb['body'][:100]}..."
        for fb in assignee_feedback
    )

    content = f"""# Phase 0.5 Analysis: {issue_key}

**Task:** {summary}
**Generated:** {datetime.now().isoformat()}
**Model:** {response.model}
**Tokens Used:** {response.tokens_used}
**Feature Type:** {response.feature_type}
**Complexity:** {response.complexity_estimate}
**Feedback Comments Processed:** {len(assignee_feedback)}

---

## Assignee Feedback

{feedback_summary}

---

## Validation

{"**PASSED** — All required sections present." if not validation_errors else "**FAILED** — Errors:"}
"""
    if validation_errors:
        for err in validation_errors:
            content += f"\n- {err}"
        content += "\n"

    content += f"""
---

## Raw LLM Response

```xml
{response.raw_content}
```
"""
    filepath.write_text(content, encoding="utf-8")
    return filepath


def _extract_original_description(context: ExecutionContext) -> str:
    """
    Extract the original task description from the execution context.

    When Phase 0 overwrites the Jira description with ADF analysis,
    the original description is preserved inside an "Original Requirements"
    expand block. The ADF→text conversion (with expand handler) renders it as:

        Original Requirements
        <original description content>

    For Phase 0.5, we extract that content. If the description hasn't been
    overwritten by Phase 0 yet, we return it as-is.
    """
    if not context.jira or not context.jira.description:
        return ""

    desc = context.jira.description

    # If the description contains Phase 0 analysis, try to extract original
    if "Phase 0: Requirements Analysis" in desc:
        # The expand block renders as "Original Requirements\n<content>\n"
        # followed by the next expand block title (Chain of Thought, etc.) or ---
        match = re.search(
            r"Original Requirements\s*\n(.*?)(?=(?:Chain of Thought|Use Cases|Definition of Ready|---))",
            desc,
            re.DOTALL,
        )
        if match:
            return match.group(1).strip()

        # Fallback: content between the first --- and the first expand block
        # This handles cases where expand titles aren't rendered
        fallback = re.search(
            r"Phase 0: Requirements Analysis.*?---\s*\n(.*?)(?=---|\Z)",
            desc,
            re.DOTALL,
        )
        if fallback:
            text = fallback.group(1).strip()
            # Skip if this captured the questions section
            if text and not text.startswith("Questions"):
                return text

    # No Phase 0 markers — description is still the original
    return desc
