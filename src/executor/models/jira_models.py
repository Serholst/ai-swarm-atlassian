"""Jira data models with cleaning and validation."""

from datetime import datetime
from typing import Optional, Any
from pydantic import BaseModel, Field, field_validator


class JiraUser(BaseModel):
    """Jira user representation."""

    account_id: str
    email: Optional[str] = None
    display_name: str


class JiraStatus(BaseModel):
    """Jira status."""

    id: str
    name: str
    status_category: str = Field(..., alias="statusCategory")

    class Config:
        """Pydantic config."""
        populate_by_name = True


class JiraIssueType(BaseModel):
    """Jira issue type."""

    id: str
    name: str  # Feature, Story, Task, Bug
    hierarchical_level: int = Field(0, description="0=Feature, -1=Story")


class JiraProject(BaseModel):
    """Jira project metadata."""

    key: str
    name: str
    id: str


class JiraComment(BaseModel):
    """Jira comment (cleaned)."""

    id: str
    author: JiraUser
    body: str  # Cleaned markdown
    created: datetime
    updated: datetime

    @field_validator("body", mode="before")
    @classmethod
    def clean_body(cls, v: Any) -> str:
        """Clean comment body - extract text content."""
        if isinstance(v, dict):
            # Jira returns ADF (Atlassian Document Format) or plain text
            if "content" in v:
                # ADF format - extract text
                return cls._extract_adf_text(v)
            return str(v)
        return str(v)

    @staticmethod
    def _extract_adf_text(adf: dict) -> str:
        """Extract plain text from Atlassian Document Format."""
        text_parts = []

        def traverse(node: dict) -> None:
            if node.get("type") == "text":
                text_parts.append(node.get("text", ""))
            if "content" in node:
                for child in node["content"]:
                    traverse(child)

        traverse(adf)
        return "".join(text_parts)


class JiraIssue(BaseModel):
    """
    Jira issue (Feature, Story, or Task) - cleaned representation.

    CRITICAL: This is the cleaned, validated version of Jira data.
    All HTML/rich text is converted to Markdown.
    """

    key: str
    id: str
    self_url: str = Field(..., alias="self")

    # Core fields
    project: JiraProject
    issue_type: JiraIssueType = Field(..., alias="issuetype")
    summary: str
    description: Optional[str] = None
    status: JiraStatus

    # People
    assignee: Optional[JiraUser] = None
    reporter: JiraUser

    # Metadata
    labels: list[str] = Field(default_factory=list)
    created: datetime
    updated: datetime

    # Hierarchy
    parent_key: Optional[str] = Field(None, description="Parent Feature key if Story")
    subtasks: list[str] = Field(default_factory=list, description="Child Story keys")

    # Custom fields (project-specific)
    custom_fields: dict[str, Any] = Field(default_factory=dict)

    # Confluence folder name (from custom "Project" field — field ID configurable via env vars)
    project_folder: str = Field("", description="Confluence folder name")

    # Direct Confluence link (from custom "Project Link" field — field ID configurable via env vars)
    project_link: str = Field("", description="Direct URL to Confluence project folder")

    class Config:
        """Pydantic config."""
        populate_by_name = True

    @field_validator("description", mode="before")
    @classmethod
    def clean_description(cls, v: Any) -> Optional[str]:
        """Clean description field - convert to Markdown."""
        if v is None:
            return None
        if isinstance(v, dict):
            # ADF format
            return JiraComment._extract_adf_text(v)
        return str(v).strip()

    def is_feature(self) -> bool:
        """Check if this is a Feature (parent)."""
        return self.issue_type.name == "Feature"

    def is_story(self) -> bool:
        """Check if this is a Story (child)."""
        return self.issue_type.name == "Story"

    def is_task(self) -> bool:
        """Check if this is a Task (blocking)."""
        return self.issue_type.name == "Task"

    def is_review_task(self) -> bool:
        """Check if this is a [REVIEW] task."""
        return self.is_task() and self.summary.startswith("[REVIEW]")
