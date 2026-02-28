"""
Validation rules for LLM response sections.

Provides validation functions to check the quality and format
of LLM-generated content before proceeding with downstream processing.
"""

import re
import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# Valid layer codes for Work Plan steps
VALID_LAYERS = {"BE", "FE", "INFRA", "DB", "QA", "DOCS", "GEN"}

# Validation thresholds
MIN_WORK_PLAN_LENGTH = 50  # Minimum characters for valid Work Plan
MAX_REASONABLE_STEPS = 15  # Warning threshold for step count
MIN_SECTION_LENGTH = 20  # Minimum characters for other sections

# Vague acceptance criteria patterns (case-insensitive)
VAGUE_PATTERNS = [
    r"should work properly",
    r"works? correctly",
    r"as expected",
    r"is correct",
    r"functions? as intended",
    r"no errors",
    r"everything works",
    r"works? fine",
    r"should be fine",
    r"properly implemented",
    r"done correctly",
]

# Placeholder values that indicate missing content
PLACEHOLDER_VALUES = {"n/a", "tbd", "todo", "tba", "none", "-", "..."}


@dataclass
class ValidationResult:
    """Result of validating an LLM response section."""

    is_valid: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    section_name: str = ""

    # Extracted metadata for debugging
    steps_found: int = 0
    layers_found: int = 0
    files_found: int = 0
    acceptance_found: int = 0
    vague_acceptance: list[str] = field(default_factory=list)
    duplicate_stories: list[tuple[int, int]] = field(default_factory=list)


def validate_work_plan(work_plan: str) -> ValidationResult:
    """
    Validate Work Plan section from LLM response.

    Validation Rules:
    1. Content exists and has minimum length (>MIN_WORK_PLAN_LENGTH chars)
    2. Has at least 1 step (pattern: "- [ ] **Step N:**")
    3. Each step should have a Layer tag (BE/FE/INFRA/DB/QA/DOCS/GEN)
    4. Step count should be reasonable (warning if >MAX_REASONABLE_STEPS)

    Args:
        work_plan: The Work Plan section content from LLM response

    Returns:
        ValidationResult with is_valid flag and any errors/warnings
    """
    result = ValidationResult(is_valid=True, section_name="Work Plan")

    # Rule 1: Content exists and has minimum length
    if not work_plan:
        result.is_valid = False
        result.errors.append("Work Plan section is empty")
        return result

    if len(work_plan.strip()) < MIN_WORK_PLAN_LENGTH:
        result.is_valid = False
        result.errors.append(
            f"Work Plan is too short ({len(work_plan.strip())} chars, minimum {MIN_WORK_PLAN_LENGTH})"
        )
        return result

    # Rule 2: Has at least one step
    step_pattern = r"-\s*\[\s*\]\s*\*\*Step\s+(\d+):\*\*"
    steps = re.findall(step_pattern, work_plan, re.IGNORECASE)
    result.steps_found = len(steps)

    if len(steps) == 0:
        result.is_valid = False
        result.errors.append("No steps found (expected format: '- [ ] **Step N:** description')")
        return result

    # Rule 3: Each step should have a Layer tag
    layer_pattern = r"\*\*Layer:\*\*\s*\[?(\w+)\]?"
    layers = re.findall(layer_pattern, work_plan, re.IGNORECASE)
    result.layers_found = len(layers)

    if len(layers) < len(steps):
        missing_count = len(steps) - len(layers)
        result.is_valid = False
        result.errors.append(
            f"Missing Layer tags: found {len(layers)} layers for {len(steps)} steps "
            f"({missing_count} missing)"
        )

    # Validate layer values
    invalid_layers = [layer for layer in layers if layer.upper() not in VALID_LAYERS]
    if invalid_layers:
        result.warnings.append(
            f"Invalid layer values: {invalid_layers}. "
            f"Valid layers: {', '.join(sorted(VALID_LAYERS))}"
        )

    # Rule 4: Reasonable step count
    if len(steps) > MAX_REASONABLE_STEPS:
        result.warnings.append(
            f"Large number of steps ({len(steps)}, threshold {MAX_REASONABLE_STEPS}). "
            "Consider if task should be broken into smaller features."
        )

    # Check for step number sequence (warning only)
    step_numbers = [int(s) for s in steps]
    expected_sequence = list(range(1, len(steps) + 1))
    if step_numbers != expected_sequence:
        result.warnings.append(
            f"Step numbers not sequential: got {step_numbers}, expected {expected_sequence}"
        )

    return result


def validate_step_fields(work_plan: str) -> ValidationResult:
    """
    Validate that each step has non-empty Files and Acceptance fields.

    Args:
        work_plan: The Work Plan section content from LLM response

    Returns:
        ValidationResult with field-level validation
    """
    result = ValidationResult(is_valid=True, section_name="Work Plan Fields")

    if not work_plan:
        return result

    # Split into step blocks
    step_block_pattern = re.compile(
        r"-\s*\[\s*\]\s*\*\*Step\s+(\d+):\*\*\s*(.+?)(?=(?:-\s*\[\s*\]\s*\*\*Step|\Z))",
        re.DOTALL | re.IGNORECASE,
    )

    steps_with_files = 0
    steps_with_acceptance = 0
    total_steps = 0

    for match in step_block_pattern.finditer(work_plan):
        step_num = int(match.group(1))
        step_content = match.group(2)
        total_steps += 1

        # Check Files field
        files_match = re.search(
            r"\*\*Files:\*\*\s*(.+?)(?=-\s*\*\*|\n\n|\Z)", step_content, re.DOTALL | re.IGNORECASE
        )
        if files_match:
            files_text = files_match.group(1).strip()
            if files_text.lower() in PLACEHOLDER_VALUES:
                result.warnings.append(f"Step {step_num}: Files field contains placeholder '{files_text}'")
            elif files_text:
                steps_with_files += 1
            else:
                result.is_valid = False
                result.errors.append(f"Step {step_num}: Files field is empty")
        else:
            result.is_valid = False
            result.errors.append(f"Step {step_num}: Missing **Files:** field")

        # Check Acceptance field
        acceptance_match = re.search(
            r"\*\*Acceptance:\*\*\s*(.+?)(?=\*\*|$)", step_content, re.DOTALL | re.IGNORECASE
        )
        if acceptance_match:
            acceptance_text = acceptance_match.group(1).strip()
            if acceptance_text.lower() in PLACEHOLDER_VALUES:
                result.warnings.append(
                    f"Step {step_num}: Acceptance field contains placeholder '{acceptance_text}'"
                )
            elif acceptance_text:
                steps_with_acceptance += 1
            else:
                result.is_valid = False
                result.errors.append(f"Step {step_num}: Acceptance field is empty")
        else:
            result.is_valid = False
            result.errors.append(f"Step {step_num}: Missing **Acceptance:** field")

    result.steps_found = total_steps
    result.files_found = steps_with_files
    result.acceptance_found = steps_with_acceptance

    return result


def validate_acceptance_quality(work_plan: str) -> ValidationResult:
    """
    Check that acceptance criteria are not vague.

    Matches against known vague patterns and flags them.

    Args:
        work_plan: The Work Plan section content from LLM response

    Returns:
        ValidationResult with vague criteria flagged as errors
    """
    result = ValidationResult(is_valid=True, section_name="Acceptance Quality")

    if not work_plan:
        return result

    # Compile vague patterns
    compiled_patterns = [re.compile(p, re.IGNORECASE) for p in VAGUE_PATTERNS]

    # Extract each acceptance criteria
    step_block_pattern = re.compile(
        r"-\s*\[\s*\]\s*\*\*Step\s+(\d+):\*\*\s*(.+?)(?=(?:-\s*\[\s*\]\s*\*\*Step|\Z))",
        re.DOTALL | re.IGNORECASE,
    )

    for match in step_block_pattern.finditer(work_plan):
        step_num = int(match.group(1))
        step_content = match.group(2)

        acceptance_match = re.search(
            r"\*\*Acceptance:\*\*\s*(.+?)(?=\*\*|$)", step_content, re.DOTALL | re.IGNORECASE
        )
        if not acceptance_match:
            continue

        acceptance_text = acceptance_match.group(1).strip()

        for pattern in compiled_patterns:
            if pattern.search(acceptance_text):
                result.is_valid = False
                result.vague_acceptance.append(f"Step {step_num}")
                result.errors.append(
                    f"Step {step_num}: Vague acceptance criteria detected "
                    f"(matched: '{pattern.pattern}'). Provide specific, verifiable criteria."
                )
                break  # One match per step is enough

    return result


def validate_duplicate_stories(work_plan: str) -> ValidationResult:
    """
    Detect duplicate or overlapping story titles.

    Uses word overlap to find potentially redundant steps.

    Args:
        work_plan: The Work Plan section content from LLM response

    Returns:
        ValidationResult with duplicate pairs as warnings
    """
    result = ValidationResult(is_valid=True, section_name="Duplicate Stories")

    if not work_plan:
        return result

    # Extract step titles
    step_pattern = re.compile(
        r"-\s*\[\s*\]\s*\*\*Step\s+(\d+):\*\*\s*([^\n]+)", re.IGNORECASE
    )

    step_titles: list[tuple[int, str]] = []
    for match in step_pattern.finditer(work_plan):
        step_num = int(match.group(1))
        title = match.group(2).strip()
        # Clean title of metadata
        title = re.sub(r"\s*-\s*\*\*Layer.*$", "", title, flags=re.IGNORECASE).strip()
        step_titles.append((step_num, title))

    # Stopwords to exclude from comparison
    stopwords = {"the", "a", "an", "and", "or", "for", "to", "in", "of", "with", "on", "is", "are"}

    def significant_words(text: str) -> set[str]:
        words = set(re.findall(r"\w+", text.lower()))
        return words - stopwords

    # Compare pairs
    for i in range(len(step_titles)):
        for j in range(i + 1, len(step_titles)):
            words_i = significant_words(step_titles[i][1])
            words_j = significant_words(step_titles[j][1])

            if not words_i or not words_j:
                continue

            overlap = words_i & words_j
            min_size = min(len(words_i), len(words_j))

            if min_size > 0 and len(overlap) / min_size > 0.6:
                pair = (step_titles[i][0], step_titles[j][0])
                result.duplicate_stories.append(pair)
                result.warnings.append(
                    f"Steps {pair[0]} and {pair[1]} may be duplicates: "
                    f"'{step_titles[i][1][:50]}' vs '{step_titles[j][1][:50]}'"
                )

    return result


def validate_dependencies(work_plan: str) -> ValidationResult:
    """
    Validate dependency references in Work Plan steps.

    Checks that referenced step numbers exist and detects circular dependencies.

    Args:
        work_plan: The Work Plan section content from LLM response

    Returns:
        ValidationResult with warnings for invalid references or circular deps
    """
    result = ValidationResult(is_valid=True, section_name="Dependencies")

    if not work_plan:
        return result

    # Extract all step numbers
    step_pattern = r"-\s*\[\s*\]\s*\*\*Step\s+(\d+):\*\*"
    all_steps = {int(s) for s in re.findall(step_pattern, work_plan, re.IGNORECASE)}

    if not all_steps:
        return result

    # Extract dependencies per step
    step_block_pattern = re.compile(
        r"-\s*\[\s*\]\s*\*\*Step\s+(\d+):\*\*\s*(.+?)(?=(?:-\s*\[\s*\]\s*\*\*Step|\Z))",
        re.DOTALL | re.IGNORECASE,
    )

    deps_map: dict[int, list[int]] = {}
    for match in step_block_pattern.finditer(work_plan):
        step_num = int(match.group(1))
        step_content = match.group(2)

        depends_match = re.search(
            r"\*\*Depends on:\*\*\s*(.+?)(?=-\s*\*\*|\n\n|\Z)", step_content, re.DOTALL | re.IGNORECASE
        )
        if depends_match:
            deps_text = depends_match.group(1).strip()
            if deps_text.lower() not in ("none", "n/a", "-", ""):
                dep_nums = [int(n) for n in re.findall(r"Step\s+(\d+)", deps_text, re.IGNORECASE)]
                deps_map[step_num] = dep_nums

                # Check for invalid references
                for dep in dep_nums:
                    if dep not in all_steps:
                        result.warnings.append(
                            f"Step {step_num}: Depends on Step {dep} which does not exist"
                        )
                    if dep == step_num:
                        result.warnings.append(
                            f"Step {step_num}: Self-dependency detected"
                        )

    # Check for circular dependencies (simple cycle detection)
    def has_cycle(node: int, visited: set[int], path: set[int]) -> bool:
        visited.add(node)
        path.add(node)
        for dep in deps_map.get(node, []):
            if dep in path:
                return True
            if dep not in visited and dep in deps_map:
                if has_cycle(dep, visited, path):
                    return True
        path.discard(node)
        return False

    visited: set[int] = set()
    for step in deps_map:
        if step not in visited:
            if has_cycle(step, visited, set()):
                result.warnings.append(
                    f"Circular dependency detected involving Step {step}"
                )

    return result


def validate_section_exists(section_name: str, content: str, min_length: int = MIN_SECTION_LENGTH) -> ValidationResult:
    """
    Validate that a response section exists and has minimum content.

    Args:
        section_name: Human-readable section name
        content: Section content
        min_length: Minimum required length

    Returns:
        ValidationResult (warnings only — missing sections don't block pipeline)
    """
    result = ValidationResult(is_valid=True, section_name=section_name)

    if not content or not content.strip():
        result.warnings.append(f"{section_name} section is empty")
        return result

    if len(content.strip()) < min_length:
        result.warnings.append(
            f"{section_name} section is too short ({len(content.strip())} chars, minimum {min_length})"
        )

    return result


def validate_response_sections(
    understanding: str,
    concerns: str,
    analysis: str,
    work_plan: str,
    definition_of_ready: str,
) -> dict[str, ValidationResult]:
    """
    Validate all LLM response sections.

    Work Plan validations produce errors (trigger retries).
    Other section validations produce warnings only.

    Args:
        understanding: Section 1 content
        concerns: Section 2 content
        analysis: Section 3 content
        work_plan: Section 4 content
        definition_of_ready: Section 5 content

    Returns:
        Dictionary mapping section name to ValidationResult
    """
    results = {}

    # Work Plan structural validation (errors trigger retries)
    results["work_plan"] = validate_work_plan(work_plan)

    # Work Plan field validation (errors trigger retries)
    results["work_plan_fields"] = validate_step_fields(work_plan)

    # Acceptance quality validation (errors trigger retries)
    results["work_plan_acceptance"] = validate_acceptance_quality(work_plan)

    # Duplicate story detection (warnings only)
    results["work_plan_duplicates"] = validate_duplicate_stories(work_plan)

    # Dependency validation (warnings only)
    results["work_plan_dependencies"] = validate_dependencies(work_plan)

    # Other sections — warnings only, don't block pipeline
    results["understanding"] = validate_section_exists("Understanding", understanding)
    results["concerns"] = validate_section_exists("Concerns", concerns, min_length=10)
    results["analysis"] = validate_section_exists("Analysis", analysis)
    results["definition_of_ready"] = validate_section_exists("Definition of Ready", definition_of_ready)

    return results


def is_response_valid(results: dict[str, ValidationResult]) -> bool:
    """Check if all validated sections passed."""
    return all(r.is_valid for r in results.values())


def get_validation_errors(results: dict[str, ValidationResult]) -> list[str]:
    """Collect all errors from validation results."""
    errors = []
    for section_name, result in results.items():
        for error in result.errors:
            errors.append(f"[{section_name}] {error}")
    return errors


def get_validation_warnings(results: dict[str, ValidationResult]) -> list[str]:
    """Collect all warnings from validation results."""
    warnings = []
    for section_name, result in results.items():
        for warning in result.warnings:
            warnings.append(f"[{section_name}] {warning}")
    return warnings


# =============================================================================
# Regex-based post-processing (avoids LLM retry for simple formatting fixes)
# =============================================================================

# Keywords → layer inference map (lowercase)
_LAYER_KEYWORDS: dict[str, str] = {
    "test": "QA", "tests": "QA", "e2e": "QA", "integration test": "QA",
    "automation": "QA", "spec": "QA",
    "migrat": "DB", "schema": "DB", "sql": "DB", "database": "DB",
    "deploy": "INFRA", "terraform": "INFRA", "k8s": "INFRA", "kubernetes": "INFRA",
    "ci/cd": "INFRA", "pipeline": "INFRA", "docker": "INFRA", "helm": "INFRA",
    "document": "DOCS", "readme": "DOCS", "confluence": "DOCS", "wiki": "DOCS",
    "ui": "FE", "frontend": "FE", "component": "FE", "css": "FE", "react": "FE",
    "api": "BE", "endpoint": "BE", "service": "BE", "backend": "BE",
    "controller": "BE", "handler": "BE", "worker": "BE",
}


def _infer_layer(step_description: str) -> str:
    """Infer a layer code from step description keywords."""
    desc_lower = step_description.lower()
    for keyword, layer in _LAYER_KEYWORDS.items():
        if keyword in desc_lower:
            return layer
    return "GEN"


def regex_fix_work_plan(work_plan: str, errors: list[str]) -> tuple[str, list[str]]:
    """
    Attempt regex-based fixes for common validation errors before burning an LLM retry.

    Handles:
    - Missing **Layer:** fields (inferred from step description keywords)
    - Missing **Files:** fields (inserted with placeholder)
    - Missing **Acceptance:** fields (inserted with placeholder)

    Args:
        work_plan: The raw work plan text from LLM response
        errors: List of validation error strings

    Returns:
        Tuple of (possibly fixed work plan, list of remaining unfixed errors)
    """
    if not work_plan or not errors:
        return work_plan, errors

    fixed = work_plan
    remaining_errors = []

    for error in errors:
        if "Missing Layer" in error or "Missing **Layer:**" in error:
            # Find steps missing Layer and insert one
            fixed = _insert_missing_layers(fixed)
        elif "Missing **Files:**" in error or "Files field is empty" in error:
            fixed = _insert_missing_field(fixed, "Files", "[To be determined]")
        elif "Missing **Acceptance:**" in error or "Acceptance field is empty" in error:
            fixed = _insert_missing_field(fixed, "Acceptance", "[To be defined]")
        else:
            remaining_errors.append(error)

    return fixed, remaining_errors


def _insert_missing_layers(work_plan: str) -> str:
    """Insert Layer tags into steps that are missing them."""
    step_pattern = re.compile(
        r"(-\s*\[\s*\]\s*\*\*Step\s+\d+:\*\*\s*)(.+?)(?=(?:-\s*\[\s*\]\s*\*\*Step|\Z))",
        re.DOTALL | re.IGNORECASE,
    )

    def _fix_step(match: re.Match) -> str:
        header = match.group(1)
        body = match.group(2)
        if re.search(r"\*\*Layer:\*\*", body, re.IGNORECASE):
            return match.group(0)  # Already has Layer
        # Infer layer from description
        desc_line = body.split("\n")[0].strip()
        layer = _infer_layer(desc_line)
        # Insert Layer as first sub-field
        indent = "  "
        return f"{header}{desc_line}\n{indent}- **Layer:** {layer}\n{indent}" + "\n".join(
            line for line in body.split("\n")[1:]
        )

    return step_pattern.sub(_fix_step, work_plan)


def _insert_missing_field(work_plan: str, field_name: str, placeholder: str) -> str:
    """Insert a missing field into steps that lack it."""
    step_pattern = re.compile(
        r"(-\s*\[\s*\]\s*\*\*Step\s+\d+:\*\*\s*.+?)(?=(?:-\s*\[\s*\]\s*\*\*Step|\Z))",
        re.DOTALL | re.IGNORECASE,
    )

    def _fix_step(match: re.Match) -> str:
        block = match.group(0)
        field_re = re.compile(rf"\*\*{field_name}:\*\*", re.IGNORECASE)
        if field_re.search(block):
            return block  # Already has field
        # Insert before the last line of the block
        lines = block.rstrip().split("\n")
        indent = "  "
        lines.append(f"{indent}- **{field_name}:** {placeholder}")
        return "\n".join(lines) + "\n"

    return step_pattern.sub(_fix_step, work_plan)
