"""Unit tests for Phase 0 validation gate (Gap 3)."""

import pytest

from src.executor.phases.phase_zero import (
    PhaseZeroResponse,
    validate_phase_zero,
    classify_validation_errors,
    MAX_PHASE0_VALIDATION_RETRIES,
)


class TestValidatePhaseZero:
    """Tests for validate_phase_zero function."""

    def _make_valid_response(self) -> PhaseZeroResponse:
        """Create a fully valid Phase 0 response."""
        return PhaseZeroResponse(
            raw_content="<phase_0_analysis>...</phase_0_analysis>",
            feature_type="new_feature",
            chain_of_thought="Step-by-step reasoning...",
            use_case=(
                '<actor>System Admin</actor>'
                '<preconditions>User is logged in</preconditions>'
                '<main_flow><step number="1">Do thing</step></main_flow>'
                '<postconditions>Thing is done</postconditions>'
            ),
            work_areas='<area layer="BE">Backend work</area>',
            risks='<risk severity="HIGH">Data loss</risk>',
            clarification_questions='<question priority="BLOCKING">What DB?</question>',
            complexity="Justification for complexity",
            complexity_estimate="M",
            definition_of_ready='<criterion testable="true">API returns 200</criterion>',
        )

    def test_valid_response_no_errors(self):
        """A fully valid response should produce no errors."""
        response = self._make_valid_response()
        errors = validate_phase_zero(response)
        assert errors == []

    def test_missing_use_case(self):
        """Missing use_case should produce an error."""
        response = self._make_valid_response()
        response.use_case = ""
        errors = validate_phase_zero(response)
        assert any("Missing <use_case>" in e for e in errors)

    def test_missing_definition_of_ready(self):
        """Missing definition_of_ready should produce an error."""
        response = self._make_valid_response()
        response.definition_of_ready = ""
        errors = validate_phase_zero(response)
        assert any("Missing <definition_of_ready>" in e for e in errors)

    def test_missing_chain_of_thought(self):
        """Missing chain_of_thought should produce an error."""
        response = self._make_valid_response()
        response.chain_of_thought = ""
        errors = validate_phase_zero(response)
        assert any("Missing <chain_of_thought>" in e for e in errors)

    def test_invalid_feature_type(self):
        """Invalid feature_type should produce an error."""
        response = self._make_valid_response()
        response.feature_type = "invalid_type"
        errors = validate_phase_zero(response)
        assert any("Invalid <feature_type>" in e for e in errors)

    def test_missing_complexity_estimate(self):
        """Missing complexity_estimate should produce an error."""
        response = self._make_valid_response()
        response.complexity_estimate = ""
        errors = validate_phase_zero(response)
        assert any("complexity estimate" in e.lower() for e in errors)


class TestClassifyValidationErrors:
    """Tests for classify_validation_errors function."""

    def test_blocking_use_case_missing(self):
        """Missing <use_case> should be classified as blocking."""
        errors = ["Missing <use_case> section"]
        blocking, non_blocking = classify_validation_errors(errors)
        assert len(blocking) == 1
        assert len(non_blocking) == 0
        assert "use_case" in blocking[0]

    def test_blocking_definition_of_ready_missing(self):
        """Missing <definition_of_ready> should be classified as blocking."""
        errors = ["Missing <definition_of_ready> section"]
        blocking, non_blocking = classify_validation_errors(errors)
        assert len(blocking) == 1
        assert "definition_of_ready" in blocking[0]

    def test_non_blocking_chain_of_thought(self):
        """Missing <chain_of_thought> should be non-blocking."""
        errors = ["Missing <chain_of_thought> section"]
        blocking, non_blocking = classify_validation_errors(errors)
        assert len(blocking) == 0
        assert len(non_blocking) == 1

    def test_non_blocking_complexity(self):
        """Missing complexity should be non-blocking."""
        errors = ["Missing complexity estimate attribute (expected S|M|L|XL)"]
        blocking, non_blocking = classify_validation_errors(errors)
        assert len(blocking) == 0
        assert len(non_blocking) == 1

    def test_mixed_errors(self):
        """Mix of blocking and non-blocking should be classified correctly."""
        errors = [
            "Missing <use_case> section",
            "Missing <chain_of_thought> section",
            "Missing <definition_of_ready> section",
            "Invalid complexity estimate: 'XXL'",
        ]
        blocking, non_blocking = classify_validation_errors(errors)
        assert len(blocking) == 2
        assert len(non_blocking) == 2

    def test_empty_errors(self):
        """Empty error list should return empty lists."""
        blocking, non_blocking = classify_validation_errors([])
        assert blocking == []
        assert non_blocking == []

    def test_non_blocking_feature_type(self):
        """Invalid feature_type should be non-blocking."""
        errors = ["Invalid <feature_type>: 'bad' (expected one of: ...)"]
        blocking, non_blocking = classify_validation_errors(errors)
        assert len(blocking) == 0
        assert len(non_blocking) == 1

    def test_non_blocking_sub_elements(self):
        """Missing sub-elements of valid sections should be non-blocking."""
        errors = [
            "Missing <actor> in <use_case>",
            "No <criterion> elements found in <definition_of_ready>",
        ]
        blocking, non_blocking = classify_validation_errors(errors)
        assert len(blocking) == 0
        assert len(non_blocking) == 2


class TestRetryConfig:
    """Verify retry configuration constants."""

    def test_max_retries_value(self):
        """MAX_PHASE0_VALIDATION_RETRIES should be 1 (2 total attempts)."""
        assert MAX_PHASE0_VALIDATION_RETRIES == 1
