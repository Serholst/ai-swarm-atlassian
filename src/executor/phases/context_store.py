"""
Context Store — serialize/deserialize ExecutionContext for iterative refinement.

Saves context after Stage 4 so that `--refine` can skip Stages 1-4 and
re-run Stage 5 with human feedback.
"""

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

from ..models.execution_context import (
    ExecutionContext,
    JiraContext,
    ConfluenceContext,
    RefinedConfluenceContext,
    RefinedDocument,
    SelectionLog,
    ProjectStatus,
)
from ..models.github_models import (
    GitHubContext,
    RepoStatus,
    RepoStructure,
    ConfigSummary,
    CodeSnippet,
)

logger = logging.getLogger(__name__)

# Schema version for forward compatibility
CONTEXT_STORE_VERSION = 1


def save_context(context: ExecutionContext, output_dir: str | Path) -> Path:
    """
    Serialize ExecutionContext to JSON for later refinement.

    Args:
        context: ExecutionContext from Stage 4
        output_dir: Directory to save the context store

    Returns:
        Path to saved JSON file
    """
    issue_dir = Path(output_dir) / context.issue_key
    issue_dir.mkdir(parents=True, exist_ok=True)
    filepath = issue_dir / f"{context.issue_key}_context_store.json"

    data = {
        "version": CONTEXT_STORE_VERSION,
        "issue_key": context.issue_key,
        "timestamp": context.timestamp.isoformat(),
        "jira": _serialize_jira(context.jira),
        "confluence": _serialize_confluence(context.confluence),
        "refined_confluence": _serialize_refined_confluence(context.refined_confluence),
        "github": context.github.to_json() if context.github else None,
        "github_status": context.github.status.value if context.github else None,
        "errors": context.errors,
    }

    filepath.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    logger.info(f"Saved context store: {filepath}")
    return filepath


def load_context(issue_key: str, output_dir: str | Path) -> Optional[ExecutionContext]:
    """
    Deserialize ExecutionContext from saved JSON.

    Args:
        issue_key: Jira issue key
        output_dir: Directory containing the context store

    Returns:
        ExecutionContext or None if not found
    """
    filepath = Path(output_dir) / issue_key / f"{issue_key}_context_store.json"

    if not filepath.exists():
        logger.warning(f"Context store not found: {filepath}")
        return None

    try:
        data = json.loads(filepath.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        logger.error(f"Failed to load context store: {e}")
        return None

    version = data.get("version", 0)
    if version != CONTEXT_STORE_VERSION:
        logger.warning(f"Context store version mismatch: {version} != {CONTEXT_STORE_VERSION}")

    context = ExecutionContext(
        issue_key=data["issue_key"],
        timestamp=datetime.fromisoformat(data["timestamp"]),
        jira=_deserialize_jira(data.get("jira")),
        confluence=_deserialize_confluence(data.get("confluence")),
        refined_confluence=_deserialize_refined_confluence(data.get("refined_confluence")),
        github=_deserialize_github(data.get("github"), data.get("github_status")),
        errors=data.get("errors", []),
    )

    logger.info(f"Loaded context store for {issue_key}")
    return context


# --- Serialization helpers ---

def _serialize_jira(jira: Optional[JiraContext]) -> Optional[dict]:
    if not jira:
        return None
    return {
        "issue_key": jira.issue_key,
        "issue_id": jira.issue_id,
        "summary": jira.summary,
        "description": jira.description,
        "issue_type": jira.issue_type,
        "status": jira.status,
        "project_key": jira.project_key,
        "project_name": jira.project_name,
        "components": jira.components,
        "labels": jira.labels,
        "assignee": jira.assignee,
        "assignee_account_id": jira.assignee_account_id,
        "reporter": jira.reporter,
        "parent_key": jira.parent_key,
        "subtasks": jira.subtasks,
        "comments": jira.comments,
        "confluence_space_key": jira.confluence_space_key,
        "project_folder": jira.project_folder,
        "project_link": jira.project_link,
        "created": jira.created,
        "updated": jira.updated,
    }


def _deserialize_jira(data: Optional[dict]) -> Optional[JiraContext]:
    if not data:
        return None
    return JiraContext(
        issue_key=data["issue_key"],
        issue_id=data["issue_id"],
        summary=data["summary"],
        description=data["description"],
        issue_type=data["issue_type"],
        status=data["status"],
        project_key=data["project_key"],
        project_name=data["project_name"],
        components=data.get("components", []),
        labels=data.get("labels", []),
        assignee=data.get("assignee"),
        assignee_account_id=data.get("assignee_account_id"),
        reporter=data.get("reporter"),
        parent_key=data.get("parent_key"),
        subtasks=data.get("subtasks", []),
        comments=data.get("comments", []),
        confluence_space_key=data.get("confluence_space_key", ""),
        project_folder=data.get("project_folder", ""),
        project_link=data.get("project_link", ""),
        created=data.get("created"),
        updated=data.get("updated"),
    )


def _serialize_confluence(conf: Optional[ConfluenceContext]) -> Optional[dict]:
    if not conf:
        return None
    return {
        "space_key": conf.space_key,
        "space_name": conf.space_name,
        "root_page_title": conf.root_page_title,
        "root_page_content": conf.root_page_content,
        "root_page_url": conf.root_page_url,
        "sdlc_rules_title": conf.sdlc_rules_title,
        "sdlc_rules_content": conf.sdlc_rules_content,
        "sdlc_rules_url": conf.sdlc_rules_url,
        "project_passport_content": conf.project_passport_content,
        "project_passport_url": conf.project_passport_url,
        "logical_architecture_content": conf.logical_architecture_content,
        "logical_architecture_url": conf.logical_architecture_url,
        "retrieval_errors": conf.retrieval_errors,
    }


def _deserialize_confluence(data: Optional[dict]) -> Optional[ConfluenceContext]:
    if not data:
        return None
    return ConfluenceContext(
        space_key=data["space_key"],
        space_name=data.get("space_name", ""),
        root_page_title=data.get("root_page_title", ""),
        root_page_content=data.get("root_page_content", ""),
        root_page_url=data.get("root_page_url", ""),
        sdlc_rules_title=data.get("sdlc_rules_title", ""),
        sdlc_rules_content=data.get("sdlc_rules_content", ""),
        sdlc_rules_url=data.get("sdlc_rules_url", ""),
        project_passport_content=data.get("project_passport_content"),
        project_passport_url=data.get("project_passport_url"),
        logical_architecture_content=data.get("logical_architecture_content"),
        logical_architecture_url=data.get("logical_architecture_url"),
        retrieval_errors=data.get("retrieval_errors", []),
    )


def _serialize_refined_confluence(rc: Optional[RefinedConfluenceContext]) -> Optional[dict]:
    if not rc:
        return None
    return {
        "project_space": rc.project_space,
        "jira_task_id": rc.jira_task_id,
        "filter_method": rc.filter_method,
        "project_status": rc.project_status.value,
        "core_documents": [
            {"title": d.title, "url": d.url, "content": d.content, "id": d.id}
            for d in rc.core_documents
        ],
        "supporting_documents": [
            {"title": d.title, "url": d.url, "content": d.content, "id": d.id}
            for d in rc.supporting_documents
        ],
        "selection_log": _serialize_selection_log(rc.selection_log),
        "missing_critical_data": rc.missing_critical_data,
        "retrieval_errors": rc.retrieval_errors,
    }


def _deserialize_refined_confluence(data: Optional[dict]) -> Optional[RefinedConfluenceContext]:
    if not data:
        return None
    return RefinedConfluenceContext(
        project_space=data["project_space"],
        jira_task_id=data["jira_task_id"],
        filter_method=data.get("filter_method", "llm_rerank"),
        project_status=ProjectStatus(data.get("project_status", "existing")),
        core_documents=[
            RefinedDocument(title=d["title"], url=d["url"], content=d["content"], id=d.get("id", ""))
            for d in data.get("core_documents", [])
        ],
        supporting_documents=[
            RefinedDocument(title=d["title"], url=d["url"], content=d["content"], id=d.get("id", ""))
            for d in data.get("supporting_documents", [])
        ],
        selection_log=_deserialize_selection_log(data.get("selection_log")),
        missing_critical_data=data.get("missing_critical_data", []),
        retrieval_errors=data.get("retrieval_errors", []),
    )


def _serialize_selection_log(log: Optional[SelectionLog]) -> Optional[dict]:
    if not log:
        return None
    return {
        "system_prompt": log.system_prompt,
        "user_prompt": log.user_prompt,
        "candidates": log.candidates,
        "raw_response": log.raw_response,
        "selected_ids": log.selected_ids,
        "model": log.model,
        "tokens_used": log.tokens_used,
    }


def _deserialize_selection_log(data: Optional[dict]) -> Optional[SelectionLog]:
    if not data:
        return None
    return SelectionLog(
        system_prompt=data["system_prompt"],
        user_prompt=data["user_prompt"],
        candidates=data["candidates"],
        raw_response=data["raw_response"],
        selected_ids=data["selected_ids"],
        model=data.get("model", "deepseek-chat"),
        tokens_used=data.get("tokens_used", 0),
    )


def _deserialize_github(data: Optional[dict], status_str: Optional[str]) -> Optional[GitHubContext]:
    if not data:
        return None

    meta = data.get("meta", {})
    status = RepoStatus(status_str) if status_str else RepoStatus.NEW_PROJECT

    structure = None
    struct_data = data.get("structure")
    if struct_data:
        structure = RepoStructure(
            tree=struct_data.get("tree", ""),
            key_directories=struct_data.get("key_directories", []),
            file_count=struct_data.get("file_count", 0),
        )

    return GitHubContext(
        repository_url=meta.get("repository_url"),
        status=status,
        discovery_source=meta.get("discovery_source", "none"),
        owner=meta.get("owner", ""),
        repo_name=meta.get("repo_name", ""),
        default_branch="main",
        primary_language=meta.get("primary_language"),
        structure=structure,
        configs=[
            ConfigSummary(
                path=c["path"],
                summary=c["summary"],
                in_confluence=c.get("in_confluence", False),
            )
            for c in data.get("configs", [])
        ],
        snippets=[
            CodeSnippet(
                path=s["path"],
                lines=s["lines"],
                content=s["content"],
                relevance=s["relevance"],
            )
            for s in data.get("snippets", [])
        ],
        recent_commits=data.get("recent_commits", []),
        open_prs=data.get("open_prs", []),
        skipped_topics=data.get("skipped_topics", []),
        retrieval_errors=data.get("retrieval_errors", []),
    )
