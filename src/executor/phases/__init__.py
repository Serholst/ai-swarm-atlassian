"""
Execution phases for the AI-SWARM pipeline.

Stages:
1. Trigger - parse_issue_key()
2. Jira Enrichment - extract_jira_context()
3. Confluence Knowledge - extract_confluence_context() or get_refined_context()
4. Data Aggregation - build_execution_context()
5. LLM Execution - execute_llm_pipeline()
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
]
