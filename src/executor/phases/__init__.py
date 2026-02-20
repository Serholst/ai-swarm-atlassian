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
    # Stage 1.5
    get_issue_status,
    # Stage 2
    extract_jira_context,
    # Stage 3 (Legacy)
    extract_confluence_context,
    # Stage 3 (Two-Stage Retrieval)
    get_refined_context,
    FILTER_SYSTEM_PROMPT,
    build_filter_prompt,
    # Stage 3c (Template Compliance)
    retrieve_confluence_templates,
    # Phase 0.5 (Feedback extraction)
    has_existing_phase0_analysis,
    extract_assignee_feedback,
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
    build_consolidated_adf_comment,
)

from .validation import (
    validate_work_plan,
    validate_step_fields,
    validate_acceptance_quality,
    validate_duplicate_stories,
    validate_dependencies,
    validate_section_exists,
    validate_response_sections,
    ValidationResult,
    is_response_valid,
    get_validation_errors,
    get_validation_warnings,
)

from .story_creator import (
    check_review_approved,
    extract_stories_from_comment,
    create_jira_stories,
    create_dependency_links,
)

from .phase_zero import (
    execute_phase_zero,
    execute_phase_zero_feedback,
    PhaseZeroResponse,
    PhaseZeroResult,
)

__all__ = [
    # Stage 1
    "parse_issue_key",
    # Stage 1.5
    "get_issue_status",
    # Stage 2
    "extract_jira_context",
    # Stage 3 (Legacy)
    "extract_confluence_context",
    # Stage 3 (Two-Stage Retrieval)
    "get_refined_context",
    "FILTER_SYSTEM_PROMPT",
    "build_filter_prompt",
    # Stage 3c (Template Compliance)
    "retrieve_confluence_templates",
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
    "build_consolidated_adf_comment",
    # Validation
    "validate_work_plan",
    "validate_step_fields",
    "validate_acceptance_quality",
    "validate_duplicate_stories",
    "validate_dependencies",
    "validate_section_exists",
    "validate_response_sections",
    "ValidationResult",
    "is_response_valid",
    "get_validation_errors",
    "get_validation_warnings",
    # Story Creator
    "check_review_approved",
    "extract_stories_from_comment",
    "create_jira_stories",
    "create_dependency_links",
    # Phase 0
    "execute_phase_zero",
    "execute_phase_zero_feedback",
    "PhaseZeroResponse",
    "PhaseZeroResult",
    # Context helpers
    "has_existing_phase0_analysis",
    "extract_assignee_feedback",
]
