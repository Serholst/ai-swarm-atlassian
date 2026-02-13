"""Unit tests for Work Plan validation rules."""

import pytest

from src.executor.phases.validation import (
    validate_work_plan,
    ValidationResult,
    MIN_WORK_PLAN_LENGTH,
    MAX_REASONABLE_STEPS,
    VALID_LAYERS,
)


class TestValidateWorkPlan:
    """Tests for validate_work_plan function."""

    def test_valid_work_plan_passes(self):
        """A properly formatted Work Plan should pass validation."""
        work_plan = """
- [ ] **Step 1:** Create API endpoint for user authentication
  - **Layer:** BE
  - **Files:** src/api/auth.py
  - **Acceptance:** Endpoint returns JWT token

- [ ] **Step 2:** Add login form component
  - **Layer:** FE
  - **Files:** src/components/LoginForm.tsx
  - **Acceptance:** Form submits credentials
"""
        result = validate_work_plan(work_plan)

        assert result.is_valid is True
        assert result.errors == []
        assert result.steps_found == 2
        assert result.layers_found == 2

    def test_empty_work_plan_fails(self):
        """Empty Work Plan should fail validation."""
        result = validate_work_plan("")

        assert result.is_valid is False
        assert "empty" in result.errors[0].lower()

    def test_none_work_plan_fails(self):
        """None Work Plan should fail validation."""
        result = validate_work_plan(None)

        assert result.is_valid is False
        assert "empty" in result.errors[0].lower()

    def test_too_short_work_plan_fails(self):
        """Work Plan shorter than MIN_WORK_PLAN_LENGTH should fail."""
        short_plan = "- [ ] **Step 1:** Do something"  # < 50 chars

        result = validate_work_plan(short_plan)

        assert result.is_valid is False
        assert "too short" in result.errors[0].lower()
        assert str(MIN_WORK_PLAN_LENGTH) in result.errors[0]

    def test_missing_steps_fails(self):
        """Work Plan without step format should fail."""
        no_steps = """
This is a long work plan description but it doesn't have any properly
formatted steps like **Step 1:** with the checkbox format.
"""
        result = validate_work_plan(no_steps)

        assert result.is_valid is False
        assert "no steps found" in result.errors[0].lower()

    def test_missing_layers_fails(self):
        """Work Plan with steps but no Layer tags should fail."""
        missing_layers = """
- [ ] **Step 1:** Create API endpoint for user authentication
  - **Files:** src/api/auth.py
  - **Acceptance:** Endpoint works

- [ ] **Step 2:** Add login form component
  - **Files:** src/components/LoginForm.tsx
  - **Acceptance:** Form works
"""
        result = validate_work_plan(missing_layers)

        assert result.is_valid is False
        assert "missing layer" in result.errors[0].lower()
        assert result.steps_found == 2
        assert result.layers_found == 0

    def test_partial_layers_fails(self):
        """Work Plan with fewer layers than steps should fail."""
        partial_layers = """
- [ ] **Step 1:** Create API endpoint
  - **Layer:** BE
  - **Files:** src/api/auth.py
  - **Acceptance:** Works

- [ ] **Step 2:** Add login form (missing Layer)
  - **Files:** src/components/LoginForm.tsx
  - **Acceptance:** Works
"""
        result = validate_work_plan(partial_layers)

        assert result.is_valid is False
        assert "missing" in result.errors[0].lower()
        assert result.steps_found == 2
        assert result.layers_found == 1

    def test_invalid_layer_value_warns(self):
        """Invalid layer value should produce warning, not error."""
        invalid_layer = """
- [ ] **Step 1:** Create API endpoint
  - **Layer:** INVALID
  - **Files:** src/api/auth.py
  - **Acceptance:** Works
"""
        result = validate_work_plan(invalid_layer)

        # Should pass (warning only), not fail
        assert result.is_valid is True
        assert len(result.warnings) > 0
        assert "invalid layer" in result.warnings[0].lower()

    def test_all_valid_layers_accepted(self):
        """All valid layer codes should be accepted."""
        for layer in VALID_LAYERS:
            work_plan = f"""
- [ ] **Step 1:** Do something with {layer} layer
  - **Layer:** {layer}
  - **Files:** some/file.py
  - **Acceptance:** It works
"""
            result = validate_work_plan(work_plan)

            assert result.is_valid is True, f"Layer {layer} should be valid"
            assert result.layers_found == 1

    def test_large_step_count_warns(self):
        """Step count exceeding MAX_REASONABLE_STEPS should produce warning."""
        # Generate 20 steps (> 15)
        steps = "\n".join([
            f"""- [ ] **Step {i}:** Task {i}
  - **Layer:** BE
  - **Files:** file{i}.py
  - **Acceptance:** Done"""
            for i in range(1, 21)
        ])

        result = validate_work_plan(steps)

        assert result.is_valid is True  # Warning, not error
        assert any("large number of steps" in w.lower() for w in result.warnings)
        assert result.steps_found == 20

    def test_non_sequential_steps_warns(self):
        """Non-sequential step numbers should produce warning."""
        non_sequential = """
- [ ] **Step 1:** First task
  - **Layer:** BE
  - **Files:** file1.py
  - **Acceptance:** Done

- [ ] **Step 3:** Third task (skipped 2)
  - **Layer:** BE
  - **Files:** file3.py
  - **Acceptance:** Done
"""
        result = validate_work_plan(non_sequential)

        assert result.is_valid is True  # Warning, not error
        assert any("not sequential" in w.lower() for w in result.warnings)

    def test_case_insensitive_layer_matching(self):
        """Layer matching should be case-insensitive."""
        mixed_case = """
- [ ] **Step 1:** Backend task
  - **Layer:** be
  - **Files:** file.py
  - **Acceptance:** Done

- [ ] **Step 2:** Frontend task
  - **Layer:** FE
  - **Files:** file.tsx
  - **Acceptance:** Done
"""
        result = validate_work_plan(mixed_case)

        assert result.is_valid is True
        assert result.layers_found == 2

    def test_validation_result_section_name(self):
        """ValidationResult should have correct section_name."""
        result = validate_work_plan("test")

        assert result.section_name == "Work Plan"


class TestValidationResultDataclass:
    """Tests for ValidationResult dataclass."""

    def test_default_values(self):
        """ValidationResult should have sensible defaults."""
        result = ValidationResult(is_valid=True)

        assert result.is_valid is True
        assert result.errors == []
        assert result.warnings == []
        assert result.section_name == ""
        assert result.steps_found == 0
        assert result.layers_found == 0

    def test_mutable_defaults_isolation(self):
        """Each instance should have isolated lists."""
        result1 = ValidationResult(is_valid=False)
        result2 = ValidationResult(is_valid=False)

        result1.errors.append("error1")

        assert "error1" in result1.errors
        assert "error1" not in result2.errors
