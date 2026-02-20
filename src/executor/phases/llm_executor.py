"""
LLM Executor - Stage 5 of the execution pipeline.

Handles:
- Prompt construction and saving
- DeepSeek API call with validation and retry
- Response parsing and saving
- Output file generation
- Metrics collection
"""

import os
import re
import time
import logging
from pathlib import Path
from datetime import datetime
from dataclasses import dataclass
from typing import Optional

from openai import OpenAI

from ..models.execution_context import ExecutionContext
from ..models.llm_metrics import LLMCallMetrics, ExecutionMetrics
from ..prompts import SYSTEM_PROMPT, build_user_prompt, build_refinement_prompt
from .validation import (
    validate_work_plan,
    validate_response_sections,
    is_response_valid,
    get_validation_errors,
    get_validation_warnings,
    ValidationResult,
)

logger = logging.getLogger(__name__)

# Retry prompt template for targeted Work Plan fixes
RETRY_PROMPT_TEMPLATE = """Your previous Work Plan section had validation errors.

## Errors Found:
{errors}

## Original Work Plan Section:
{original_work_plan}

## Instructions:
Please regenerate ONLY the Work Plan section (### 4. Work Plan) with the following corrections:

1. Each step MUST follow format: - [ ] **Step N:** [description]
2. Each step MUST include **Layer:** [BE/FE/INFRA/DB/QA/DOCS/GEN]
3. Each step MUST include **Files:** [expected files]
4. Each step MUST include **Acceptance:** [verification criteria]

Layer codes:
- BE - Backend, API, Microservices
- FE - Frontend, UI/UX
- INFRA - Terraform, K8s, CI/CD
- DB - Migrations, Schema changes
- QA - Tests, Automation
- DOCS - Documentation
- GEN - General/Cross-cutting

Return ONLY the corrected Work Plan section, starting with "### 4. Work Plan".
"""

# Constants
WORK_PLAN_TRUNCATE_LIMIT = 3000  # Max chars to include in retry prompt


@dataclass
class LLMResponse:
    """Response from LLM execution."""

    # Raw response
    raw_content: str

    # Parsed sections
    understanding: str = ""
    concerns: str = ""
    analysis: str = ""
    work_plan: str = ""
    definition_of_ready: str = ""

    # Metadata
    model: str = ""
    tokens_used: int = 0
    tokens_in: int = 0  # prompt_tokens
    tokens_out: int = 0  # completion_tokens
    finish_reason: str = ""
    retry_failed: bool = False  # True if retry API call failed


@dataclass
class ExecutionOutput:
    """Output files from execution."""

    context_file: Path
    prompt_file: Path
    reasoning_file: Path
    plan_file: Path
    selection_file: Optional[Path] = None  # LLM document selection log
    metrics_file: Optional[Path] = None  # LLM usage metrics


class LLMExecutor:
    """Stage 5: LLM Execution with DeepSeek API."""

    # Validation retry settings
    MAX_RETRIES = 2  # Max 3 total attempts (1 initial + 2 retries)

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: str = "deepseek-chat",
        api_base: str = "https://api.deepseek.com/v1",
        temperature: float = 0.2,
        max_tokens: int = 8192,
        output_dir: str = "outputs",
        max_retries: Optional[int] = None,
    ):
        """
        Initialize LLM Executor.

        Args:
            api_key: DeepSeek API key (defaults to DEEPSEEK_API_KEY env var)
            model: Model name (deepseek-chat or deepseek-coder)
            api_base: API base URL
            temperature: Sampling temperature
            max_tokens: Maximum response tokens
            output_dir: Directory for output files
            max_retries: Override default max retries for validation failures
        """
        self.api_key = api_key or os.getenv("DEEPSEEK_API_KEY")
        if not self.api_key:
            raise ValueError("DEEPSEEK_API_KEY not found in environment")

        self.model = model
        self.api_base = api_base
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.output_dir = Path(output_dir)
        self.max_retries = max_retries if max_retries is not None else self.MAX_RETRIES

        # Initialize OpenAI client with DeepSeek endpoint
        self.client = OpenAI(
            api_key=self.api_key,
            base_url=self.api_base,
        )

        # Metrics tracking (reset per execution)
        self.metrics: Optional[ExecutionMetrics] = None

        logger.info(f"LLM Executor initialized: model={model}, base={api_base}")

    def execute(self, context: ExecutionContext) -> tuple[LLMResponse, ExecutionOutput]:
        """
        Execute Stage 5: LLM call with validation loop and output generation.

        Args:
            context: ExecutionContext from Stage 4

        Returns:
            Tuple of (LLMResponse, ExecutionOutput)
        """
        issue_key = context.issue_key
        logger.info(f"Stage 5: Executing LLM for {issue_key}")

        # Initialize metrics for this execution
        self.metrics = ExecutionMetrics(issue_key=issue_key)

        # Create output directory
        issue_dir = self.output_dir / issue_key
        issue_dir.mkdir(parents=True, exist_ok=True)

        # Build prompts
        user_prompt = build_user_prompt(context)

        # Step 1: Save context
        context_file = self._save_context(issue_dir, context)
        logger.info(f"Saved context: {context_file}")

        # Step 1.5: Save selection log (if available from Stage 3)
        selection_file = self._save_selection(issue_dir, context)
        if selection_file:
            logger.info(f"Saved selection: {selection_file}")

        # Step 2: Save prompt BEFORE LLM call
        prompt_file = self._save_prompt(issue_dir, context, user_prompt)
        logger.info(f"Saved prompt: {prompt_file}")

        # Step 3: Call DeepSeek API with validation loop
        response = None
        validation_result: Optional[ValidationResult] = None
        max_attempts = self.max_retries + 1  # 1 initial + N retries

        for attempt in range(1, max_attempts + 1):
            start_time = time.time()

            if attempt == 1:
                # First attempt: full prompt
                logger.info(f"Calling DeepSeek API ({self.model}), attempt {attempt}...")
                response = self._call_llm(user_prompt)
            else:
                # Retry: targeted fix prompt for Work Plan only
                logger.info(f"Retry {attempt - 1}/{self.max_retries}: Requesting Work Plan fix...")
                response = self._call_llm_retry(
                    original_response=response,
                    validation_errors=validation_result.errors if validation_result else [],
                    attempt=attempt,
                )

            duration_ms = int((time.time() - start_time) * 1000)

            # Validate all response sections
            section_results = validate_response_sections(
                understanding=response.understanding,
                concerns=response.concerns,
                analysis=response.analysis,
                work_plan=response.work_plan,
                definition_of_ready=response.definition_of_ready,
            )

            # Aggregate into single ValidationResult for backward compat
            all_errors = get_validation_errors(section_results)
            all_warnings = get_validation_warnings(section_results)
            wp_result = section_results.get("work_plan", ValidationResult(is_valid=True))

            validation_result = ValidationResult(
                is_valid=is_response_valid(section_results),
                errors=all_errors,
                warnings=all_warnings,
                section_name="all_sections",
                steps_found=wp_result.steps_found,
                layers_found=wp_result.layers_found,
            )

            # Record metrics for this call
            call_metrics = LLMCallMetrics(
                tokens_in=response.tokens_in,
                tokens_out=response.tokens_out,
                tokens_total=response.tokens_used,
                model=response.model,
                call_purpose="planning" if attempt == 1 else "retry",
                attempt_number=attempt,
                duration_ms=duration_ms,
                validation_attempts=1,
                validation_passed=validation_result.is_valid,
                validation_errors=validation_result.errors.copy(),
            )
            self.metrics.add_call(call_metrics)

            if validation_result.is_valid:
                logger.info(
                    f"Attempt {attempt}: Validation PASSED "
                    f"({validation_result.steps_found} steps, {validation_result.layers_found} layers)"
                )
                break
            else:
                logger.warning(
                    f"Attempt {attempt}: Validation FAILED - {validation_result.errors}"
                )
                if validation_result.warnings:
                    logger.warning(f"Warnings: {validation_result.warnings}")

                if attempt == max_attempts:
                    self.metrics.max_retries_hit = True
                    logger.error(
                        f"Max retries ({self.max_retries}) reached. "
                        "Proceeding with potentially invalid Work Plan."
                    )

        logger.info(
            f"LLM response finalized: {response.tokens_used} tokens, "
            f"{response.finish_reason}, {self.metrics.retry_count} retries"
        )

        # Step 4: Save reasoning (full response)
        reasoning_file = self._save_reasoning(issue_dir, context, response)
        logger.info(f"Saved reasoning: {reasoning_file}")

        # Step 5: Extract and save work plan
        plan_file = self._save_plan(issue_dir, context, response)
        logger.info(f"Saved plan: {plan_file}")

        # Step 6: Save metrics
        metrics_file = self._save_metrics(issue_dir, context)
        logger.info(f"Saved metrics: {metrics_file}")

        output = ExecutionOutput(
            context_file=context_file,
            prompt_file=prompt_file,
            reasoning_file=reasoning_file,
            plan_file=plan_file,
            selection_file=selection_file,
            metrics_file=metrics_file,
        )

        logger.info(f"Stage 5 complete: {issue_key}")
        return response, output

    def execute_refinement(
        self,
        context: ExecutionContext,
        feedback: str,
        previous_plan: str,
        version: int = 2,
    ) -> tuple[LLMResponse, ExecutionOutput]:
        """
        Execute Stage 5 refinement: re-run LLM with feedback on previous plan.

        Args:
            context: ExecutionContext (loaded from context store)
            feedback: Human feedback text
            previous_plan: Previous work plan text
            version: Refinement version (2, 3, ...)

        Returns:
            Tuple of (LLMResponse, ExecutionOutput)
        """
        issue_key = context.issue_key
        logger.info(f"Stage 5 (Refinement v{version}): Executing LLM for {issue_key}")

        # Initialize metrics
        self.metrics = ExecutionMetrics(issue_key=issue_key)

        # Create output directory
        issue_dir = self.output_dir / issue_key
        issue_dir.mkdir(parents=True, exist_ok=True)

        # Build refinement prompt
        user_prompt = build_refinement_prompt(context, feedback, previous_plan, version)

        # Save prompt
        version_suffix = f"_v{version}"
        prompt_file = issue_dir / f"{issue_key}_prompt{version_suffix}.md"
        prompt_file.write_text(
            f"# Refinement Prompt v{version} for {issue_key}\n\n"
            f"Generated: {datetime.now().isoformat()}\n"
            f"Model: {self.model}\n\n---\n\n"
            f"## System Prompt\n\n```\n{SYSTEM_PROMPT}\n```\n\n---\n\n"
            f"## User Prompt\n\n{user_prompt}\n",
            encoding="utf-8",
        )

        # Call LLM with same validation loop as execute()
        response = None
        validation_result: Optional[ValidationResult] = None
        max_attempts = self.max_retries + 1

        for attempt in range(1, max_attempts + 1):
            start_time = time.time()

            if attempt == 1:
                logger.info(f"Calling DeepSeek API ({self.model}), refinement v{version}, attempt {attempt}...")
                response = self._call_llm(user_prompt)
            else:
                logger.info(f"Retry {attempt - 1}/{self.max_retries}: Requesting Work Plan fix...")
                response = self._call_llm_retry(
                    original_response=response,
                    validation_errors=validation_result.errors if validation_result else [],
                    attempt=attempt,
                )

            duration_ms = int((time.time() - start_time) * 1000)

            # Validate
            section_results = validate_response_sections(
                understanding=response.understanding,
                concerns=response.concerns,
                analysis=response.analysis,
                work_plan=response.work_plan,
                definition_of_ready=response.definition_of_ready,
            )

            all_errors = get_validation_errors(section_results)
            all_warnings = get_validation_warnings(section_results)
            wp_result = section_results.get("work_plan", ValidationResult(is_valid=True))

            validation_result = ValidationResult(
                is_valid=is_response_valid(section_results),
                errors=all_errors,
                warnings=all_warnings,
                section_name="all_sections",
                steps_found=wp_result.steps_found,
                layers_found=wp_result.layers_found,
            )

            call_metrics = LLMCallMetrics(
                tokens_in=response.tokens_in,
                tokens_out=response.tokens_out,
                tokens_total=response.tokens_used,
                model=response.model,
                call_purpose="refinement" if attempt == 1 else "retry",
                attempt_number=attempt,
                duration_ms=duration_ms,
                validation_attempts=1,
                validation_passed=validation_result.is_valid,
                validation_errors=validation_result.errors.copy(),
            )
            self.metrics.add_call(call_metrics)

            if validation_result.is_valid:
                logger.info(f"Attempt {attempt}: Validation PASSED")
                break
            else:
                logger.warning(f"Attempt {attempt}: Validation FAILED - {validation_result.errors}")
                if attempt == max_attempts:
                    self.metrics.max_retries_hit = True
                    logger.error(f"Max retries reached for refinement v{version}")

        # Save outputs with version suffix
        reasoning_file = issue_dir / f"{issue_key}_reasoning{version_suffix}.md"
        reasoning_file.write_text(
            f"# Refined Reasoning v{version} for {issue_key}\n\n"
            f"Generated: {datetime.now().isoformat()}\n"
            f"Model: {response.model}\n"
            f"Tokens Used: {response.tokens_used}\n\n---\n\n"
            f"{response.raw_content}\n",
            encoding="utf-8",
        )

        plan_file = issue_dir / f"{issue_key}_plan{version_suffix}.md"
        summary = context.jira.summary if context.jira else issue_key
        plan_file.write_text(
            f"# Refined Work Plan v{version}: {issue_key}\n\n"
            f"**Task:** {summary}\n"
            f"**Generated:** {datetime.now().isoformat()}\n"
            f"**Model:** {response.model}\n"
            f"**Feedback:** {feedback[:200]}\n\n---\n\n"
            f"## Steps\n\n{response.work_plan or '[Section not found]'}\n",
            encoding="utf-8",
        )

        metrics_file = issue_dir / f"{issue_key}_metrics{version_suffix}.md"
        metrics_file.write_text(
            self.metrics.to_markdown() if self.metrics else "No metrics collected.",
            encoding="utf-8",
        )

        # Context file not re-saved (same as original)
        context_file = issue_dir / f"{issue_key}_context.md"

        output = ExecutionOutput(
            context_file=context_file,
            prompt_file=prompt_file,
            reasoning_file=reasoning_file,
            plan_file=plan_file,
            metrics_file=metrics_file,
        )

        logger.info(f"Refinement v{version} complete: {issue_key}")
        return response, output

    def _call_llm(self, user_prompt: str) -> LLMResponse:
        """Call DeepSeek API and return parsed response."""
        try:
            completion = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=self.temperature,
                max_tokens=self.max_tokens,
            )

            raw_content = completion.choices[0].message.content or ""
            tokens_in = completion.usage.prompt_tokens if completion.usage else 0
            tokens_out = completion.usage.completion_tokens if completion.usage else 0
            tokens_used = tokens_in + tokens_out
            finish_reason = completion.choices[0].finish_reason or ""

            response = LLMResponse(
                raw_content=raw_content,
                model=self.model,
                tokens_used=tokens_used,
                tokens_in=tokens_in,
                tokens_out=tokens_out,
                finish_reason=finish_reason,
            )

            # Parse sections from response
            self._parse_response_sections(response)

            return response

        except Exception as e:
            logger.error(f"LLM API call failed: {e}")
            raise

    def _call_llm_retry(
        self,
        original_response: LLMResponse,
        validation_errors: list[str],
        attempt: int,
    ) -> LLMResponse:
        """
        Call LLM to fix only the Work Plan section.

        Uses a targeted prompt asking to fix specific validation errors.
        Preserves other sections from the original response.

        Args:
            original_response: The original LLM response with invalid Work Plan
            validation_errors: List of validation errors to fix
            attempt: Current attempt number

        Returns:
            LLMResponse with fixed Work Plan (other sections preserved)
        """
        # Build targeted retry prompt
        retry_prompt = RETRY_PROMPT_TEMPLATE.format(
            errors="\n".join(f"- {e}" for e in validation_errors),
            original_work_plan=original_response.work_plan[:WORK_PLAN_TRUNCATE_LIMIT],
        )

        try:
            completion = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {
                        "role": "system",
                        "content": "You are fixing a validation error in a work plan. "
                        "Follow the format instructions exactly.",
                    },
                    {"role": "user", "content": retry_prompt},
                ],
                temperature=0.1,  # Lower temperature for deterministic fix
                max_tokens=4096,  # Less tokens needed for just one section
            )

            fixed_work_plan = completion.choices[0].message.content or ""
            tokens_in = completion.usage.prompt_tokens if completion.usage else 0
            tokens_out = completion.usage.completion_tokens if completion.usage else 0
            tokens_used = tokens_in + tokens_out
            finish_reason = completion.choices[0].finish_reason or ""

            # Create new response with fixed work plan, preserving other sections
            fixed_response = LLMResponse(
                raw_content=original_response.raw_content,  # Keep original raw
                understanding=original_response.understanding,
                concerns=original_response.concerns,
                analysis=original_response.analysis,
                work_plan=fixed_work_plan.strip(),  # Use fixed version
                definition_of_ready=original_response.definition_of_ready,
                model=self.model,
                tokens_used=tokens_used,
                tokens_in=tokens_in,
                tokens_out=tokens_out,
                finish_reason=finish_reason,
            )

            return fixed_response

        except Exception as e:
            logger.error(f"Retry call failed: {type(e).__name__}: {str(e)[:200]}")
            # Mark failure and return original response
            original_response.retry_failed = True
            return original_response

    def _parse_response_sections(self, response: LLMResponse) -> None:
        """Parse response into sections."""
        content = response.raw_content

        # Extract sections using headers
        sections = {
            "understanding": r"###?\s*1\.\s*Understanding.*?\n(.*?)(?=###?\s*2\.|$)",
            "concerns": r"###?\s*2\.\s*Concerns.*?\n(.*?)(?=###?\s*3\.|$)",
            "analysis": r"###?\s*3\.\s*Analysis.*?\n(.*?)(?=###?\s*4\.|$)",
            "work_plan": r"###?\s*4\.\s*Work Plan.*?\n(.*?)(?=###?\s*5\.|$)",
            "definition_of_ready": r"###?\s*5\.\s*Definition of Ready.*?\n(.*?)(?=$)",
        }

        for field, pattern in sections.items():
            match = re.search(pattern, content, re.DOTALL | re.IGNORECASE)
            if match:
                setattr(response, field, match.group(1).strip())

    def _save_context(self, issue_dir: Path, context: ExecutionContext) -> Path:
        """Save raw context to file."""
        filepath = issue_dir / f"{context.issue_key}_context.md"

        content = f"""# Context for {context.issue_key}

Generated: {context.timestamp.isoformat()}

---

{context.build_prompt_context()}
"""
        filepath.write_text(content, encoding="utf-8")
        return filepath

    def _save_prompt(self, issue_dir: Path, context: ExecutionContext, user_prompt: str) -> Path:
        """Save full prompt to file BEFORE LLM call."""
        filepath = issue_dir / f"{context.issue_key}_prompt.md"

        content = f"""# LLM Prompt for {context.issue_key}

Generated: {datetime.now().isoformat()}
Model: {self.model}
Temperature: {self.temperature}
Max Tokens: {self.max_tokens}

---

## System Prompt

```
{SYSTEM_PROMPT}
```

---

## User Prompt

{user_prompt}
"""
        filepath.write_text(content, encoding="utf-8")
        return filepath

    def _save_reasoning(self, issue_dir: Path, context: ExecutionContext, response: LLMResponse) -> Path:
        """Save full LLM response with metadata."""
        filepath = issue_dir / f"{context.issue_key}_reasoning.md"

        content = f"""# Agent Reasoning for {context.issue_key}

Generated: {datetime.now().isoformat()}
Model: {response.model}
Tokens Used: {response.tokens_used}
Finish Reason: {response.finish_reason}

---

{response.raw_content}
"""
        filepath.write_text(content, encoding="utf-8")
        return filepath

    def _save_plan(self, issue_dir: Path, context: ExecutionContext, response: LLMResponse) -> Path:
        """Extract and save work plan section."""
        filepath = issue_dir / f"{context.issue_key}_plan.md"

        # Build plan document
        summary = context.jira.summary if context.jira else context.issue_key

        content = f"""# Work Plan: {context.issue_key}

**Task:** {summary}
**Generated:** {datetime.now().isoformat()}
**Model:** {response.model}

---

## Understanding

{response.understanding or '[Section not found in response]'}

---

## Concerns & Uncertainties

{response.concerns or '[Section not found in response]'}

---

## Analysis

{response.analysis or '[Section not found in response]'}

---

## Steps

{response.work_plan or '[Section not found in response]'}

---

## Definition of Ready

{response.definition_of_ready or '[Section not found in response]'}
"""
        filepath.write_text(content, encoding="utf-8")
        return filepath

    def _save_metrics(self, issue_dir: Path, context: ExecutionContext) -> Path:
        """Save LLM usage metrics to markdown file."""
        filepath = issue_dir / f"{context.issue_key}_metrics.md"

        if self.metrics:
            content = self.metrics.to_markdown()
        else:
            content = f"# LLM Metrics: {context.issue_key}\n\nNo metrics collected."

        filepath.write_text(content, encoding="utf-8")
        return filepath

    def _save_selection(self, issue_dir: Path, context: ExecutionContext) -> Optional[Path]:
        """Save LLM document selection log if available."""
        if not context.refined_confluence or not context.refined_confluence.selection_log:
            return None

        selection_log = context.refined_confluence.selection_log
        filepath = issue_dir / f"{context.issue_key}_selection.md"

        content = f"""# Document Selection Log: {context.issue_key}

**Generated:** {datetime.now().isoformat()}
**Model:** {selection_log.model}
**Tokens Used:** {selection_log.tokens_used}

---

{selection_log.format_markdown()}
"""
        filepath.write_text(content, encoding="utf-8")
        return filepath


def execute_llm_pipeline(
    context: ExecutionContext,
    api_key: Optional[str] = None,
    model: str = "deepseek-chat",
    output_dir: str = "outputs",
) -> tuple[LLMResponse, ExecutionOutput]:
    """
    Convenience function to execute Stage 5.

    Args:
        context: ExecutionContext from Stage 4
        api_key: DeepSeek API key (optional, uses env var)
        model: Model name
        output_dir: Output directory

    Returns:
        Tuple of (LLMResponse, ExecutionOutput)
    """
    executor = LLMExecutor(
        api_key=api_key,
        model=model,
        output_dir=output_dir,
    )
    return executor.execute(context)
