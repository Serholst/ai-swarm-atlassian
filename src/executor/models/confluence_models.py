"""Confluence data models with HTML cleaning."""

from datetime import datetime
from typing import Optional, Any
from pydantic import BaseModel, Field, field_validator


class ConfluenceSpace(BaseModel):
    """Confluence space metadata."""

    key: str
    name: str
    id: str


class ConfluencePage(BaseModel):
    """
    Confluence page (cleaned).

    CRITICAL: Body is cleaned from HTML and converted to Markdown.
    """

    id: str
    title: str
    space: ConfluenceSpace
    status: str  # current, archived

    # Content (cleaned!)
    body: str = Field(..., description="Cleaned Markdown content")

    # Metadata
    version: int
    created_at: datetime = Field(..., alias="created")
    updated_at: datetime = Field(..., alias="updated")

    # URL
    url: str = Field(..., description="Direct link to page")

    # Labels
    labels: list[str] = Field(default_factory=list)

    # Parent page (for hierarchy)
    parent_id: Optional[str] = None

    class Config:
        """Pydantic config."""
        populate_by_name = True

    @field_validator("body", mode="before")
    @classmethod
    def clean_body(cls, v: Any) -> str:
        """
        Clean HTML body to Markdown.

        Confluence returns HTML in 'storage' format.
        We need to clean it to prevent garbage data.
        """
        if isinstance(v, dict):
            # Get storage format
            html_content = v.get("storage", {}).get("value", "")
        else:
            html_content = str(v)

        # Clean HTML (will be implemented in utility)
        from ..utils.html_cleaner import clean_confluence_html

        return clean_confluence_html(html_content)


class ProjectPassport(BaseModel):
    """
    Project Passport structure from SDLC template.

    This is the parsed representation of a Project Passport page.
    """

    # From Confluence page
    page_id: str
    page_url: str
    version: int

    # Identity & Ownership
    project_name: str
    project_key: str  # Extracted from page or manually set
    business_value: str

    # Technology Stack
    tech_stack: dict[str, Any] = Field(
        default_factory=dict, description="Infrastructure components (DB, brokers, modules)"
    )

    # Repositories
    repositories: dict[str, str] = Field(
        default_factory=dict,
        description="GitHub repo URLs (frontend, backend, iac)",
    )

    # Environments
    environments: dict[str, dict[str, str]] = Field(
        default_factory=dict,
        description="Dev, Stage, Prod with deploy links and config specs",
    )

    # Raw markdown (for reference)
    raw_content: str = Field(..., description="Full cleaned Markdown content")

    @field_validator("project_key", mode="before")
    @classmethod
    def validate_project_key(cls, v: Any) -> str:
        """Validate project key format."""
        if not v:
            return "[DATA MISSING]"
        return str(v).upper()


class LogicalArchitecture(BaseModel):
    """
    Logical Architecture structure from SDLC template.

    This is the parsed representation of a Logical Architecture page.
    """

    # From Confluence page
    page_id: str
    page_url: str
    version: int

    # Component Diagram (Modules)
    modules: list[dict[str, str]] = Field(
        default_factory=list,
        description="List of modules with responsibilities and dependencies",
    )

    # Data Flow (Flows)
    flows: list[dict[str, Any]] = Field(
        default_factory=list, description="Business scenarios and data flows"
    )

    # Contracts & Interfaces
    contracts: dict[str, Any] = Field(
        default_factory=dict, description="API contracts and data schemas"
    )

    # Constraints
    constraints: list[str] = Field(
        default_factory=list, description="Architectural constraints"
    )

    # Raw markdown (for reference)
    raw_content: str = Field(..., description="Full cleaned Markdown content")


class SDLCRules(BaseModel):
    """
    Parsed SDLC & Workflows Rules page.

    This is loaded once and used for validation.
    """

    page_id: str
    page_url: str
    version: int

    # Rules content
    rules_content: str = Field(..., description="Full SDLC rules in Markdown")

    # Parsed sections
    global_imperatives: str
    operational_mode: str
    naming_conventions: str
    workflow_protocol: str
    error_handling: str
    quality_gates: str
