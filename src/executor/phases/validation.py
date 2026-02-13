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


def validate_response_sections(
    understanding: str,
    concerns: str,
    analysis: str,
    work_plan: str,
    definition_of_ready: str,
) -> dict[str, ValidationResult]:
    """
    Validate all LLM response sections.

    Currently only validates Work Plan (highest failure rate).
    Other sections may be added in future.

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

    # Work Plan is the critical section - validate it
    results["work_plan"] = validate_work_plan(work_plan)

    # Future: Add validation for other sections as needed
    # results["understanding"] = validate_understanding(understanding)
    # results["definition_of_ready"] = validate_dor(definition_of_ready)

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
