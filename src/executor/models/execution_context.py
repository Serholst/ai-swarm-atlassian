"""
Execution context models for the 5-stage pipeline.

Stage 1: Trigger → issue_key
Stage 2: Jira Enrichment → JiraContext
Stage 3a: Confluence Knowledge → RefinedConfluenceContext (Two-Stage Retrieval)
Stage 3b: GitHub Context → GitHubContext (Confluence-filtered)
Stage 4: Data Aggregation → ExecutionContext
Stage 5: LLM Execution → uses ExecutionContext.prompt_context
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, Union, TYPE_CHECKING
from enum import Enum

if TYPE_CHECKING:
    from .github_models import GitHubContext  # noqa: F401


class ProjectStatus(Enum):
    """Project status based on documentation state."""
    EXISTING = "existing"      # Has Passport + Architecture with content
    INCOMPLETE = "incomplete"  # Pages exist but are empty or lack content
    NEW_PROJECT = "new"        # Folder exists but no mandatory docs
    NOT_FOUND = "not_found"    # Folder doesn't exist
    BRAND_NEW = "brand_new"    # No project_link AND no project_folder (greenfield)


class ContextLocationError(Exception):
    """Raised when project space/folder cannot be resolved."""
    pass


@dataclass
class JiraContext:
    """Stage 2 output: Enriched Jira data."""

    # Identity
    issue_key: str
    issue_id: str

    # Core fields
    summary: str
    description: str  # Cleaned Markdown
    issue_type: str  # Feature, Story, Task
    status: str

    # Project
    project_key: str
    project_name: str

    # Classification
    components: list[str] = field(default_factory=list)
    labels: list[str] = field(default_factory=list)

    # People
    assignee: Optional[str] = None
    assignee_account_id: Optional[str] = None
    reporter: Optional[str] = None

    # Hierarchy
    parent_key: Optional[str] = None
    subtasks: list[str] = field(default_factory=list)

    # Comments (from Jira)
    comments: list[dict] = field(default_factory=list)  # [{author, created, body}]

    # Derived: Confluence space key (from labels or project)
    confluence_space_key: str = ""

    # Confluence folder name (from custom "Project" field)
    project_folder: str = ""

    # Direct Confluence link (from custom "Project Link" field)
    project_link: str = ""

    # Timestamps
    created: Optional[str] = None
    updated: Optional[str] = None


@dataclass
class ConfluenceContext:
    """Stage 3 output: Knowledge from Confluence."""

    # Space info
    space_key: str
    space_name: str = ""

    # Root page (space homepage)
    root_page_title: str = ""
    root_page_content: str = ""  # Markdown
    root_page_url: str = ""

    # SDLC Rules page
    sdlc_rules_title: str = ""
    sdlc_rules_content: str = ""  # Markdown
    sdlc_rules_url: str = ""

    # Optional: Project Passport
    project_passport_content: Optional[str] = None
    project_passport_url: Optional[str] = None

    # Optional: Logical Architecture
    logical_architecture_content: Optional[str] = None
    logical_architecture_url: Optional[str] = None

    # Retrieval status
    retrieval_errors: list[str] = field(default_factory=list)


@dataclass
class RefinedDocument:
    """A document selected by the Two-Stage Retrieval process."""
    title: str
    url: str
    content: str  # Markdown (text only)
    id: str = ""


@dataclass
class SelectionLog:
    """Captures LLM document selection reasoning for transparency."""

    # Input
    system_prompt: str
    user_prompt: str
    candidates: list[dict]  # [{id, title, excerpt}]

    # Output
    raw_response: str
    selected_ids: list[str]

    # Metadata
    model: str = "deepseek-chat"
    tokens_used: int = 0

    def format_markdown(self) -> str:
        """Format selection log as markdown for output file."""
        # Format candidates table
        candidates_table = "| ID | Title | Excerpt |\n|---|---|---|\n"
        for c in self.candidates:
            excerpt = c.get("excerpt", "")[:100].replace("\n", " ").replace("|", "\\|")
            candidates_table += f"| {c['id']} | {c['title']} | {excerpt}... |\n"

        # Format selection result
        selection_result = ""
        for c in self.candidates:
            status = "✅ SELECTED" if c["id"] in self.selected_ids else "❌ rejected"
            selection_result += f"- [{status}] `{c['id']}` - {c['title']}\n"

        return f"""## System Prompt

```
{self.system_prompt}
```

## User Prompt

{self.user_prompt}

## Candidates ({len(self.candidates)} pages)

{candidates_table}

## LLM Raw Response

```json
{self.raw_response}
```

## Selection Result

{selection_result if selection_result else "_No candidates to evaluate_"}

## Metadata

- Model: {self.model}
- Tokens Used: {self.tokens_used}
"""


@dataclass
class RefinedConfluenceContext:
    """Stage 3 output with Two-Stage Retrieval (API Search → LLM Reranking)."""

    # Meta
    project_space: str
    jira_task_id: str
    filter_method: str = "llm_rerank"
    project_status: ProjectStatus = ProjectStatus.EXISTING

    # Core context (Mandatory Path - Project Passport, Logical Architecture)
    core_documents: list[RefinedDocument] = field(default_factory=list)

    # Supporting context (Discovery Path - LLM filtered)
    supporting_documents: list[RefinedDocument] = field(default_factory=list)

    # LLM Selection Log (captures reasoning for document selection)
    selection_log: Optional[SelectionLog] = None

    # Missing critical data (informational, not error)
    missing_critical_data: list[str] = field(default_factory=list)

    # Retrieval errors (actual errors only)
    retrieval_errors: list[str] = field(default_factory=list)

    def is_new_project(self) -> bool:
        """Check if this is a new project needing documentation."""
        return self.project_status == ProjectStatus.NEW_PROJECT

    def to_json(self) -> dict:
        """Convert to output JSON structure."""
        return {
            "meta": {
                "project_space": self.project_space,
                "jira_task_id": self.jira_task_id,
                "filter_method": self.filter_method,
                "project_status": self.project_status.value,
            },
            "core_context": {
                "description": "Critical project boundaries (Passport, Architecture). MUST be prioritized.",
                "documents": [
                    {"title": doc.title, "url": doc.url, "content": doc.content}
                    for doc in self.core_documents
                ],
            },
            "supporting_context": {
                "description": "Contextual documents selected by CTO filter based on the task.",
                "documents": [
                    {"title": doc.title, "url": doc.url, "content": doc.content}
                    for doc in self.supporting_documents
                ],
            },
            "missing_critical_data": self.missing_critical_data,
        }


@dataclass
class ConfluenceTemplate:
    """A Confluence template page retrieved from the Templates folder."""
    doc_type: str  # e.g., "Project Passport", "Logical Architecture"
    title: str
    content: str  # Full page content (markdown)
    page_id: str = ""
    url: str = ""


@dataclass
class ExecutionContext:
    """Stage 4 output: Unified context for LLM execution."""

    # Metadata
    issue_key: str
    timestamp: datetime = field(default_factory=datetime.now)

    # Stage 2 & 3 outputs
    jira: Optional[JiraContext] = None
    confluence: Optional[ConfluenceContext] = None
    refined_confluence: Optional[RefinedConfluenceContext] = None  # Two-Stage Retrieval
    github: Optional["GitHubContext"] = None  # GitHub context (Confluence-filtered)

    # Confluence templates (from Templates/Patterns folder)
    confluence_templates: list[ConfluenceTemplate] = field(default_factory=list)

    # Aggregation status
    errors: list[str] = field(default_factory=list)

    def build_prompt_context(self) -> str:
        """Build unified context string for LLM prompt."""
        sections = []

        # Header
        sections.append(f"# Task Context: {self.issue_key}")
        sections.append(f"Generated: {self.timestamp.isoformat()}")
        sections.append("")

        # Jira section
        if self.jira:
            sections.append("---")
            sections.append("")
            sections.append("## Jira Issue")
            sections.append("")
            sections.append(f"**Key:** {self.jira.issue_key}")
            sections.append(f"**Title:** {self.jira.summary}")
            sections.append(f"**Type:** {self.jira.issue_type}")
            sections.append(f"**Status:** {self.jira.status}")
            sections.append(f"**Project:** {self.jira.project_name} ({self.jira.project_key})")
            sections.append(f"**Components:** {', '.join(self.jira.components) or 'None'}")
            sections.append(f"**Labels:** {', '.join(self.jira.labels) or 'None'}")
            sections.append(f"**Assignee:** {self.jira.assignee or 'Unassigned'}")

            if self.jira.parent_key:
                sections.append(f"**Parent:** {self.jira.parent_key}")
            if self.jira.subtasks:
                sections.append(f"**Subtasks:** {', '.join(self.jira.subtasks)}")

            sections.append("")
            sections.append("### Description")
            sections.append("")
            sections.append(self.jira.description or "[No description provided]")
            sections.append("")

            # Comments
            if self.jira.comments:
                sections.append("### Comments")
                sections.append("")
                for comment in self.jira.comments:
                    author = comment.get("author", "Unknown")
                    created = comment.get("created", "")
                    body = comment.get("body", "")
                    sections.append(f"**{author}** ({created}):")
                    sections.append(f"> {body}")
                    sections.append("")

        # Refined Confluence section (Two-Stage Retrieval)
        if self.refined_confluence:
            sections.append("---")
            sections.append("")
            sections.append("## Project Knowledge Base")
            sections.append("")
            sections.append(f"**Space:** {self.refined_confluence.project_space}")
            sections.append(f"**Status:** {self.refined_confluence.project_status.value}")
            sections.append("")

            # Documentation gaps signal (BRAND_NEW, NEW_PROJECT, or INCOMPLETE)
            status = self.refined_confluence.project_status
            if status in (ProjectStatus.BRAND_NEW, ProjectStatus.NEW_PROJECT, ProjectStatus.INCOMPLETE):
                if status == ProjectStatus.BRAND_NEW:
                    sections.append("### BRAND NEW PROJECT")
                    sections.append("")
                    sections.append("**IMPORTANT:** This is a greenfield project with no existing Confluence documentation.")
                elif status == ProjectStatus.NEW_PROJECT:
                    sections.append("### NEW PROJECT - DOCUMENTATION MISSING")
                    sections.append("")
                    sections.append("**IMPORTANT:** The project folder exists in Confluence but mandatory documentation pages are missing.")
                elif status == ProjectStatus.INCOMPLETE:
                    sections.append("### INCOMPLETE PROJECT DOCUMENTATION")
                    sections.append("")
                    sections.append("**IMPORTANT:** Some mandatory documentation pages exist but are empty or incomplete.")

                sections.append("")
                if self.refined_confluence.missing_critical_data:
                    sections.append("**Documentation gaps:**")
                    for item in self.refined_confluence.missing_critical_data:
                        sections.append(f"- {item}")
                    sections.append("")

                sections.append("Your work plan MUST include steps to create or fill these pages.")

                # Use templates if available, otherwise fall back to defaults
                if self.confluence_templates:
                    sections.append("")
                    sections.append("**Refer to the TEMPLATE COMPLIANCE section below for exact page structures.**")
                else:
                    sections.append("1. **Project Passport** page with sections:")
                    sections.append("   - Identity & Ownership")
                    sections.append("   - Technology Stack")
                    sections.append("   - Repositories")
                    sections.append("   - Environments")
                    sections.append("2. **Logical Architecture** page with sections:")
                    sections.append("   - Component Diagram")
                    sections.append("   - Data Flow")
                    sections.append("   - Contracts & Interfaces")
                    sections.append("   - Constraints")

                sections.append("")
                sections.append("Use `[DOCS]` layer for documentation creation/update steps.")
                sections.append("")

            # Core documents (Mandatory Path)
            if self.refined_confluence.core_documents:
                sections.append("### Core Documentation (Mandatory)")
                sections.append("")
                for doc in self.refined_confluence.core_documents:
                    sections.append(f"#### {doc.title}")
                    sections.append(f"URL: {doc.url}")
                    sections.append("")
                    sections.append(doc.content)
                    sections.append("")

            # Supporting documents (Discovery Path)
            if self.refined_confluence.supporting_documents:
                sections.append("### Supporting Documentation (LLM Selected)")
                sections.append("")
                for doc in self.refined_confluence.supporting_documents:
                    sections.append(f"#### {doc.title}")
                    sections.append(f"URL: {doc.url}")
                    sections.append("")
                    sections.append(doc.content)
                    sections.append("")

            # Retrieval errors
            if self.refined_confluence.retrieval_errors:
                sections.append("### Retrieval Warnings")
                for err in self.refined_confluence.retrieval_errors:
                    sections.append(f"- {err}")
                sections.append("")

        # Legacy Confluence section (backwards compatibility)
        elif self.confluence:
            sections.append("---")
            sections.append("")
            sections.append("## Project Knowledge Base")
            sections.append("")
            sections.append(f"**Space:** {self.confluence.space_name} ({self.confluence.space_key})")
            sections.append("")

            if self.confluence.root_page_content:
                sections.append(f"### {self.confluence.root_page_title or 'Space Homepage'}")
                sections.append("")
                sections.append(self.confluence.root_page_content)
                sections.append("")

            if self.confluence.sdlc_rules_content:
                sections.append("---")
                sections.append("")
                sections.append("## SDLC & Workflow Rules")
                sections.append("")
                sections.append(self.confluence.sdlc_rules_content)
                sections.append("")

            if self.confluence.project_passport_content:
                sections.append("---")
                sections.append("")
                sections.append("## Project Passport")
                sections.append("")
                sections.append(self.confluence.project_passport_content)
                sections.append("")

            if self.confluence.retrieval_errors:
                sections.append("")
                sections.append("### Retrieval Warnings")
                for err in self.confluence.retrieval_errors:
                    sections.append(f"- {err}")
                sections.append("")

        # GitHub section
        if self.github:
            sections.append("---")
            sections.append("")
            sections.append("## Codebase Context (GitHub)")
            sections.append("")
            sections.append(self.github.format_markdown())

        # Global errors
        if self.errors:
            sections.append("---")
            sections.append("")
            sections.append("## Context Errors")
            for err in self.errors:
                sections.append(f"- {err}")
            sections.append("")

        return "\n".join(sections)

    def is_valid(self) -> bool:
        """Check if context has minimum required data.

        Only summary is required — description may be empty for backlog
        items that Phase 0 is designed to analyze.
        """
        return (
            self.jira is not None
            and bool(self.jira.summary)
        )

    def is_new_project(self) -> bool:
        """Check if this is a new project based on refined context."""
        if self.refined_confluence:
            return self.refined_confluence.is_new_project()
        return False
