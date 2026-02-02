"""Workflow state models based on SDLC template."""

from enum import Enum
from typing import Optional
from pydantic import BaseModel, Field


class WorkflowStatus(str, Enum):
    """
    Jira workflow statuses - exact names from SDLC template.

    These are case-sensitive and must match Jira board columns exactly.
    """

    BACKLOG = "Backlog"
    AI_TO_DO = "AI-TO-DO"
    ANALYSIS = "Analysis"
    HUMAN_PLAN_REVIEW = "Human Plan Review"
    READY_FOR_DEV = "Ready for Dev"
    IN_PROGRESS = "In Progress"
    REVIEW = "Review"
    DEPLOYMENT = "Deployment"
    DONE = "Done"


class ExecutorContext(BaseModel):
    """
    Execution context for a Feature.

    CRITICAL: This is STATELESS - all fields are references to external systems.
    No local caching or storage.
    """

    # Feature identity
    jira_key: str = Field(..., description="Jira Feature key (e.g., AI-123)")
    current_status: WorkflowStatus = Field(..., description="Current Jira status")

    # Project metadata (from Project Passport)
    project_key: str = Field(..., description="Project key extracted from issue (e.g., 'AI')")
    project_name: str = Field(..., description="Full project name from Passport")

    # Confluence references
    confluence_space_key: str = Field(..., description="Confluence Space key")
    project_passport_page_id: str = Field(..., description="Project Passport page ID")
    logical_architecture_page_id: Optional[str] = Field(
        None, description="Logical Architecture page ID"
    )

    # GitHub references
    github_repo: str = Field(..., description="GitHub repository (owner/repo)")

    # Feature details
    feature_title: str = Field(..., description="Feature summary/title")
    feature_description: str = Field(..., description="Feature description")

    class Config:
        """Pydantic config."""
        use_enum_values = True


class PhaseContext(BaseModel):
    """Context specific to a phase execution."""

    phase_name: str
    executor_context: ExecutorContext

    # Chain of Thoughts tracking
    cot_log: list[str] = Field(default_factory=list, description="Reasoning log")

    def add_cot(self, entry: str) -> None:
        """Add Chain of Thoughts entry."""
        self.cot_log.append(entry)

    def get_cot_summary(self) -> str:
        """Get formatted CoT summary."""
        return "\n".join(f"- {entry}" for entry in self.cot_log)
