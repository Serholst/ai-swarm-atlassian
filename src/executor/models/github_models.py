"""
GitHub context models for the execution pipeline.

Provides structured data from GitHub repositories to enhance LLM context
with actual codebase information.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class RepoStatus(Enum):
    """Repository discovery status."""
    EXISTS = "exists"           # Repository found and accessible
    NOT_FOUND = "not_found"     # URL provided but repo doesn't exist or inaccessible
    NEW_PROJECT = "new_project" # No URL found - repository to be created


@dataclass
class RepoStructure:
    """Repository directory structure (filtered to key directories)."""
    tree: str                           # Markdown tree representation
    key_directories: list[str] = field(default_factory=list)  # src/, lib/, tests/, etc.
    file_count: int = 0
    primary_language: Optional[str] = None


@dataclass
class ConfigSummary:
    """Summary of a configuration file."""
    path: str                           # File path in repo (e.g., "package.json")
    summary: str                        # Key information extracted
    in_confluence: bool = False         # True if details already in Confluence docs


@dataclass
class CodeSnippet:
    """Relevant code reference from the repository."""
    path: str                           # File path
    lines: str                          # Line range (e.g., "42-67")
    content: str                        # Actual code snippet
    relevance: str                      # Why it's relevant to the task


@dataclass
class GitHubContext:
    """
    Stage 3b output: GitHub repository context.

    Complements Confluence context with actual codebase information.
    Designed for context saturation without duplicating Confluence data.
    """

    # Discovery
    repository_url: Optional[str] = None
    status: RepoStatus = RepoStatus.NEW_PROJECT
    discovery_source: str = "none"      # "jira_description" or "confluence_passport"

    # Repository metadata
    owner: str = ""
    repo_name: str = ""
    default_branch: str = "main"
    primary_language: Optional[str] = None

    # Structure (always fetched - rarely in Confluence)
    structure: Optional[RepoStructure] = None

    # Config summaries (deduplicated against Confluence)
    configs: list[ConfigSummary] = field(default_factory=list)

    # Code snippets (relevant to task)
    snippets: list[CodeSnippet] = field(default_factory=list)

    # Recent activity (never in Confluence)
    recent_commits: list[str] = field(default_factory=list)  # Last 5-10 commit messages
    open_prs: list[str] = field(default_factory=list)        # Titles of open PRs

    # Deduplication tracking
    skipped_topics: list[str] = field(default_factory=list)  # Topics covered in Confluence

    # Errors during retrieval
    retrieval_errors: list[str] = field(default_factory=list)

    def is_available(self) -> bool:
        """Check if GitHub context was successfully retrieved."""
        return self.status == RepoStatus.EXISTS

    def format_markdown(self) -> str:
        """Format GitHub context as markdown for LLM prompt."""
        if self.status == RepoStatus.NEW_PROJECT:
            return "**GitHub:** New project - repository to be created"

        if self.status == RepoStatus.NOT_FOUND:
            return f"**GitHub:** Repository not found or inaccessible ({self.repository_url})"

        sections = []

        # Header
        sections.append(f"**Repository:** [{self.owner}/{self.repo_name}]({self.repository_url})")
        if self.primary_language:
            sections.append(f"**Primary Language:** {self.primary_language}")
        sections.append(f"**Default Branch:** {self.default_branch}")
        sections.append("")

        # Structure
        if self.structure:
            sections.append("### Repository Structure")
            sections.append("")
            sections.append("```")
            sections.append(self.structure.tree)
            sections.append("```")
            sections.append("")

        # Config summaries
        if self.configs:
            sections.append("### Configuration Files")
            sections.append("")
            for config in self.configs:
                if config.in_confluence:
                    sections.append(f"- **{config.path}**: _(details in Confluence)_")
                else:
                    sections.append(f"- **{config.path}**: {config.summary}")
            sections.append("")

        # Code snippets
        if self.snippets:
            sections.append("### Relevant Code References")
            sections.append("")
            for snippet in self.snippets:
                sections.append(f"#### {snippet.path} (lines {snippet.lines})")
                sections.append(f"_{snippet.relevance}_")
                sections.append("")
                sections.append("```")
                sections.append(snippet.content)
                sections.append("```")
                sections.append("")

        # Recent activity
        if self.recent_commits:
            sections.append("### Recent Commits")
            sections.append("")
            for commit in self.recent_commits[:5]:
                sections.append(f"- {commit}")
            sections.append("")

        if self.open_prs:
            sections.append("### Open Pull Requests")
            sections.append("")
            for pr in self.open_prs[:5]:
                sections.append(f"- {pr}")
            sections.append("")

        # Deduplication note
        if self.skipped_topics:
            sections.append("### Skipped (Already in Confluence)")
            sections.append("")
            sections.append(f"The following topics are documented in Confluence: {', '.join(self.skipped_topics)}")
            sections.append("")

        # Errors
        if self.retrieval_errors:
            sections.append("### Retrieval Warnings")
            sections.append("")
            for err in self.retrieval_errors:
                sections.append(f"- {err}")
            sections.append("")

        return "\n".join(sections)

    def to_json(self) -> dict:
        """Convert to JSON structure for output files."""
        return {
            "meta": {
                "repository_url": self.repository_url,
                "status": self.status.value,
                "discovery_source": self.discovery_source,
                "owner": self.owner,
                "repo_name": self.repo_name,
                "primary_language": self.primary_language,
            },
            "structure": {
                "tree": self.structure.tree if self.structure else "",
                "key_directories": self.structure.key_directories if self.structure else [],
                "file_count": self.structure.file_count if self.structure else 0,
            } if self.structure else None,
            "configs": [
                {"path": c.path, "summary": c.summary, "in_confluence": c.in_confluence}
                for c in self.configs
            ],
            "snippets": [
                {"path": s.path, "lines": s.lines, "content": s.content, "relevance": s.relevance}
                for s in self.snippets
            ],
            "recent_commits": self.recent_commits,
            "open_prs": self.open_prs,
            "skipped_topics": self.skipped_topics,
            "retrieval_errors": self.retrieval_errors,
        }
