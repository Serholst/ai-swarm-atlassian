"""LLM usage metrics tracking for the execution pipeline."""

import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class LLMCallMetrics:
    """Metrics for a single LLM API call."""

    # Token counts (from OpenAI API response.usage)
    tokens_in: int = 0  # prompt_tokens
    tokens_out: int = 0  # completion_tokens
    tokens_total: int = 0  # total_tokens

    # Validation tracking
    validation_attempts: int = 0
    validation_passed: bool = False
    validation_errors: list[str] = field(default_factory=list)

    # Call metadata
    model: str = ""
    call_purpose: str = ""  # "planning" | "document_selection" | "retry"
    attempt_number: int = 1  # Which attempt (1, 2, 3...)

    # Timing
    duration_ms: int = 0
    timestamp: datetime = field(default_factory=datetime.now)

    def __post_init__(self):
        """Ensure tokens_total is calculated if not provided."""
        if self.tokens_total == 0 and (self.tokens_in or self.tokens_out):
            self.tokens_total = self.tokens_in + self.tokens_out


@dataclass
class ExecutionMetrics:
    """Aggregated metrics for entire pipeline execution."""

    # All individual calls
    calls: list[LLMCallMetrics] = field(default_factory=list)

    # Retry tracking
    max_retries_hit: bool = False
    issue_key: str = ""

    def add_call(self, call: LLMCallMetrics) -> None:
        """Add a call to the metrics collection."""
        self.calls.append(call)

    @property
    def total_tokens_in(self) -> int:
        """Total prompt tokens across all calls."""
        return sum(c.tokens_in for c in self.calls)

    @property
    def total_tokens_out(self) -> int:
        """Total completion tokens across all calls."""
        return sum(c.tokens_out for c in self.calls)

    @property
    def total_tokens(self) -> int:
        """Total tokens across all calls."""
        return self.total_tokens_in + self.total_tokens_out

    @property
    def total_validation_attempts(self) -> int:
        """Total validation attempts across all calls."""
        return sum(c.validation_attempts for c in self.calls)

    @property
    def total_validation_failures(self) -> int:
        """Total validation failures across all calls."""
        return sum(1 for c in self.calls if c.validation_attempts > 0 and not c.validation_passed)

    @property
    def retry_count(self) -> int:
        """Number of retry calls made."""
        return sum(1 for c in self.calls if c.call_purpose == "retry")

    @property
    def validation_failure_rate(self) -> float:
        """Percentage of validation failures."""
        total = self.total_validation_attempts
        if total == 0:
            return 0.0
        return (self.total_validation_failures / total) * 100

    def to_markdown(self) -> str:
        """Format metrics as markdown for output file."""
        lines = [
            f"# LLM Metrics: {self.issue_key}",
            "",
            f"Generated: {datetime.now().isoformat()}",
            "",
            "## Summary",
            "",
            "| Metric | Value |",
            "|--------|-------|",
            f"| Total Tokens In | {self.total_tokens_in:,} |",
            f"| Total Tokens Out | {self.total_tokens_out:,} |",
            f"| Total Tokens | {self.total_tokens:,} |",
            f"| Validation Attempts | {self.total_validation_attempts} |",
            f"| Validation Failures | {self.total_validation_failures} |",
            f"| Retries Used | {self.retry_count} |",
            f"| Max Retries Hit | {'Yes' if self.max_retries_hit else 'No'} |",
            "",
            "## Call Log",
            "",
            "| # | Purpose | Tokens In | Tokens Out | Validation | Duration |",
            "|---|---------|-----------|------------|------------|----------|",
        ]

        for i, call in enumerate(self.calls, 1):
            validation_status = "N/A"
            if call.validation_attempts > 0:
                validation_status = "PASSED" if call.validation_passed else "FAILED"

            duration_str = f"{call.duration_ms / 1000:.1f}s" if call.duration_ms > 0 else "N/A"

            lines.append(
                f"| {i} | {call.call_purpose} | {call.tokens_in:,} | "
                f"{call.tokens_out:,} | {validation_status} | {duration_str} |"
            )

        # Add validation errors if any
        failed_calls = [c for c in self.calls if c.validation_errors]
        if failed_calls:
            lines.extend(["", "## Validation Errors", ""])
            for call in failed_calls:
                lines.append(f"### Attempt {call.attempt_number}")
                for error in call.validation_errors:
                    lines.append(f"- {error}")
                lines.append("")

        return "\n".join(lines)


@dataclass
class StageMetrics:
    """Metrics for a single pipeline stage."""

    stage_name: str
    duration_ms: int = 0
    success: bool = True
    error: Optional[str] = None
    metadata: dict = field(default_factory=dict)


@dataclass
class PipelineMetrics:
    """Full pipeline metrics covering all stages."""

    issue_key: str = ""
    start_time: datetime = field(default_factory=datetime.now)
    end_time: Optional[datetime] = None

    # Per-stage metrics
    stages: list[StageMetrics] = field(default_factory=list)

    # LLM-specific metrics (from Stage 5)
    llm_metrics: Optional[ExecutionMetrics] = None

    # Validation stats
    validation_pass: bool = False
    validation_errors: int = 0
    validation_warnings: int = 0

    # Story stats
    stories_extracted: int = 0
    stories_created: int = 0

    # Confidence
    overall_confidence: float = 0.0

    def add_stage(self, stage: StageMetrics) -> None:
        """Add a completed stage to the metrics."""
        self.stages.append(stage)

    def finalize(self) -> None:
        """Mark pipeline as complete."""
        self.end_time = datetime.now()

    @property
    def total_duration_ms(self) -> int:
        """Total pipeline duration in milliseconds."""
        if self.end_time:
            return int((self.end_time - self.start_time).total_seconds() * 1000)
        return sum(s.duration_ms for s in self.stages)

    def to_markdown(self) -> str:
        """Format pipeline metrics as markdown."""
        lines = [
            f"# Pipeline Metrics: {self.issue_key}",
            "",
            f"Generated: {datetime.now().isoformat()}",
            f"Total Duration: {self.total_duration_ms / 1000:.1f}s",
            "",
            "## Pipeline Summary",
            "",
            "| Metric | Value |",
            "|--------|-------|",
            f"| Issue Key | {self.issue_key} |",
            f"| Total Duration | {self.total_duration_ms / 1000:.1f}s |",
            f"| Stages | {len(self.stages)} |",
            f"| Validation Passed | {'Yes' if self.validation_pass else 'No'} |",
            f"| Validation Errors | {self.validation_errors} |",
            f"| Validation Warnings | {self.validation_warnings} |",
            f"| Stories Extracted | {self.stories_extracted} |",
            f"| Stories Created | {self.stories_created} |",
            f"| Overall Confidence | {self.overall_confidence:.0%} |",
            "",
            "## Stage Breakdown",
            "",
            "| Stage | Duration | Status | Details |",
            "|-------|----------|--------|---------|",
        ]

        for stage in self.stages:
            status = "OK" if stage.success else "FAILED"
            details = stage.error or ""
            if stage.metadata:
                meta_parts = [f"{k}={v}" for k, v in stage.metadata.items()]
                details = ", ".join(meta_parts)
            lines.append(
                f"| {stage.stage_name} | {stage.duration_ms / 1000:.1f}s | "
                f"{status} | {details} |"
            )

        # Include LLM metrics if available
        if self.llm_metrics:
            lines.extend([
                "",
                "## LLM Metrics",
                "",
                "| Metric | Value |",
                "|--------|-------|",
                f"| Total Tokens | {self.llm_metrics.total_tokens:,} |",
                f"| Tokens In | {self.llm_metrics.total_tokens_in:,} |",
                f"| Tokens Out | {self.llm_metrics.total_tokens_out:,} |",
                f"| Retries | {self.llm_metrics.retry_count} |",
                f"| Max Retries Hit | {'Yes' if self.llm_metrics.max_retries_hit else 'No'} |",
            ])

        return "\n".join(lines)

    def to_json(self) -> dict:
        """Export pipeline metrics as JSON dict."""
        return {
            "issue_key": self.issue_key,
            "start_time": self.start_time.isoformat(),
            "end_time": self.end_time.isoformat() if self.end_time else None,
            "total_duration_ms": self.total_duration_ms,
            "validation": {
                "passed": self.validation_pass,
                "errors": self.validation_errors,
                "warnings": self.validation_warnings,
            },
            "stories": {
                "extracted": self.stories_extracted,
                "created": self.stories_created,
                "overall_confidence": self.overall_confidence,
            },
            "stages": [
                {
                    "name": s.stage_name,
                    "duration_ms": s.duration_ms,
                    "success": s.success,
                    "error": s.error,
                    "metadata": s.metadata,
                }
                for s in self.stages
            ],
            "llm": {
                "total_tokens": self.llm_metrics.total_tokens if self.llm_metrics else 0,
                "retries": self.llm_metrics.retry_count if self.llm_metrics else 0,
            },
        }
