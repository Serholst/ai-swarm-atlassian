"""
Execution phases for the AI-SWARM pipeline.

Stages:
1. Trigger - parse_issue_key()
2. Jira Enrichment - extract_jira_context()
3. Confluence Knowledge - extract_confluence_context() or get_refined_context()
4. Data Aggregation - build_execution_context()
5. LLM Execution - execute_llm_pipeline()
5.5. Analysis & Decomposition - handle_analysis_decomposition()
     - Creates blocking review Story
     - Adds Technical Decomposition comment
     - Adds Executor Rationale (CoT) comment
     - Adds Clarification Questions comment (optional)
"""

from .context_builder import (
    # Stage 1
    parse_issue_key,
    # Stage 2
    extract_jira_context,
    # Stage 3 (Legacy)
    extract_confluence_context,
    # Stage 3 (Two-Stage Retrieval)
    get_refined_context,
    FILTER_SYSTEM_PROMPT,
    build_filter_prompt,
    # Stage 4
    build_execution_context,
    # Full Pipelines
    build_context_pipeline,
    build_refined_context_pipeline,
)

from .llm_executor import (
    LLMExecutor,
    LLMResponse,
    ExecutionOutput,
    execute_llm_pipeline,
)

from .post_execution import (
    handle_post_execution,
    determine_outcome,
    ExecutionOutcome,
    TransitionResult,
)

from .decomposition import (
    handle_analysis_decomposition,
    parse_llm_response,
    extract_stories,
    extract_questions,
    build_decomposition_comment,
    build_cot_comment,
    build_clarifications_comment,
)

from .validation import (
    validate_work_plan,
    validate_response_sections,
    ValidationResult,
    is_response_valid,
    get_validation_errors,
    get_validation_warnings,
)

__all__ = [
    # Stage 1
    "parse_issue_key",
    # Stage 2
    "extract_jira_context",
    # Stage 3 (Legacy)
    "extract_confluence_context",
    # Stage 3 (Two-Stage Retrieval)
    "get_refined_context",
    "FILTER_SYSTEM_PROMPT",
    "build_filter_prompt",
    # Stage 4
    "build_execution_context",
    # Full Pipelines
    "build_context_pipeline",
    "build_refined_context_pipeline",
    # Stage 5
    "LLMExecutor",
    "LLMResponse",
    "ExecutionOutput",
    "execute_llm_pipeline",
    # Post-Execution (Jira Transitions)
    "handle_post_execution",
    "determine_outcome",
    "ExecutionOutcome",
    "TransitionResult",
    # Analysis & Decomposition
    "handle_analysis_decomposition",
    "parse_llm_response",
    "extract_stories",
    "extract_questions",
    "build_decomposition_comment",
    "build_cot_comment",
    "build_clarifications_comment",
    # Validation
    "validate_work_plan",
    "validate_response_sections",
    "ValidationResult",
    "is_response_valid",
    "get_validation_errors",
    "get_validation_warnings",
]
