"""Unit tests for GitHub context gate function and helpers."""

from src.executor.models.github_models import (
    GitHubContext,
    GitHubFetchDecision,
    RepoStatus,
)
from src.executor.models.execution_context import (
    JiraContext,
    ProjectStatus,
    RefinedConfluenceContext,
    RefinedDocument,
)
from src.executor.phases.context_builder import (
    extract_github_url,
    extract_github_url_from_comments,
    should_fetch_github_context,
    _is_code_related_task,
)

# =============================================================================
# Helpers
# =============================================================================


def _make_jira(
    description: str = "",
    labels: list[str] | None = None,
    comments: list[dict] | None = None,
    project_link: str = "",
    assignee_account_id: str | None = None,
    issue_type: str = "Feature",
) -> JiraContext:
    return JiraContext(
        issue_key="TEST-1",
        issue_id="1",
        summary="Test task",
        description=description,
        issue_type=issue_type,
        status="AI To Do",
        project_key="TEST",
        project_name="Test Project",
        labels=labels or [],
        comments=comments or [],
        project_link=project_link,
        assignee_account_id=assignee_account_id,
    )


def _make_confluence(passport_content: str = "") -> RefinedConfluenceContext | None:
    if not passport_content:
        return None
    return RefinedConfluenceContext(
        project_space="TEST",
        jira_task_id="TEST-1",
        project_status=ProjectStatus.EXISTING,
        core_documents=[
            RefinedDocument(
                title="Project Passport",
                url="https://confluence.example.com/x/abc",
                content=passport_content,
            ),
        ],
    )


# =============================================================================
# TestExtractGithubUrl
# =============================================================================


class TestExtractGithubUrl:
    """Tests for extract_github_url (existing function, extend coverage)."""

    def test_https_url(self):
        text = "See https://github.com/acme/web-app for details"
        assert extract_github_url(text) == "https://github.com/acme/web-app"

    def test_ssh_url(self):
        text = "Clone git@github.com:acme/web-app.git and build"
        assert extract_github_url(text) == "https://github.com/acme/web-app"

    def test_no_url(self):
        assert extract_github_url("No repo link here") is None

    def test_url_with_trailing_slash(self):
        text = "https://github.com/acme/web-app/ is the repo"
        assert extract_github_url(text) == "https://github.com/acme/web-app"

    def test_url_with_git_suffix(self):
        text = "https://github.com/acme/web-app.git"
        assert extract_github_url(text) == "https://github.com/acme/web-app"


# =============================================================================
# TestExtractGithubUrlFromComments
# =============================================================================


class TestExtractGithubUrlFromComments:
    """Tests for comment-based URL extraction."""

    def test_finds_url_in_assignee_comment(self):
        comments = [
            {
                "author": "Alice",
                "account_id": "abc123",
                "created": "2024-01-15",
                "body": "Use https://github.com/acme/backend for this task",
            },
        ]
        result = extract_github_url_from_comments(comments, assignee_account_id="abc123")
        assert result == "https://github.com/acme/backend"

    def test_ignores_non_assignee_comment(self):
        comments = [
            {
                "author": "Bob",
                "account_id": "other",
                "created": "2024-01-15",
                "body": "Use https://github.com/acme/backend",
            },
        ]
        result = extract_github_url_from_comments(comments, assignee_account_id="abc123")
        assert result is None

    def test_no_assignee_filter_returns_any(self):
        comments = [
            {
                "author": "Bob",
                "account_id": "other",
                "created": "2024-01-15",
                "body": "Use https://github.com/acme/backend",
            },
        ]
        result = extract_github_url_from_comments(comments, assignee_account_id=None)
        assert result == "https://github.com/acme/backend"

    def test_returns_most_recent_url(self):
        comments = [
            {
                "author": "A",
                "account_id": "a1",
                "created": "2024-01-10",
                "body": "https://github.com/acme/old-repo",
            },
            {
                "author": "A",
                "account_id": "a1",
                "created": "2024-01-20",
                "body": "https://github.com/acme/new-repo",
            },
        ]
        result = extract_github_url_from_comments(comments, assignee_account_id="a1")
        assert result == "https://github.com/acme/new-repo"

    def test_empty_comments(self):
        assert extract_github_url_from_comments([], assignee_account_id="abc") is None

    def test_comment_without_url(self):
        comments = [
            {
                "author": "Alice",
                "account_id": "abc123",
                "created": "2024-01-15",
                "body": "Please check the implementation",
            },
        ]
        result = extract_github_url_from_comments(comments, assignee_account_id="abc123")
        assert result is None


# =============================================================================
# TestIsCodeRelatedTask
# =============================================================================


class TestIsCodeRelatedTask:
    """Tests for _is_code_related_task."""

    def test_default_is_code_related(self):
        assert _is_code_related_task(_make_jira()) is True

    def test_documentation_label_not_code_related(self):
        assert _is_code_related_task(_make_jira(labels=["documentation"])) is False

    def test_docs_only_label_not_code_related(self):
        assert _is_code_related_task(_make_jira(labels=["docs-only"])) is False

    def test_no_code_label_not_code_related(self):
        assert _is_code_related_task(_make_jira(labels=["no-code"])) is False

    def test_process_label_not_code_related(self):
        assert _is_code_related_task(_make_jira(labels=["process"])) is False

    def test_feature_type_documentation_only(self):
        jira = _make_jira()
        assert _is_code_related_task(jira, feature_type="documentation_only") is False

    def test_feature_type_process(self):
        jira = _make_jira()
        assert _is_code_related_task(jira, feature_type="process") is False

    def test_feature_type_new_feature_is_code(self):
        jira = _make_jira()
        assert _is_code_related_task(jira, feature_type="new_feature") is True

    def test_feature_type_update_existing_is_code(self):
        jira = _make_jira()
        assert _is_code_related_task(jira, feature_type="update_existing") is True

    def test_labels_case_insensitive(self):
        assert _is_code_related_task(_make_jira(labels=["Documentation"])) is False
        assert _is_code_related_task(_make_jira(labels=["DOCS-ONLY"])) is False

    def test_mixed_labels_one_non_code(self):
        """If any label matches non-code, task is not code-related."""
        jira = _make_jira(labels=["backend", "documentation", "urgent"])
        assert _is_code_related_task(jira) is False


# =============================================================================
# TestShouldFetchGithubContext
# =============================================================================


class TestShouldFetchGithubContext:
    """Tests for the main gate function."""

    def test_non_code_task_skips(self):
        jira = _make_jira(
            description="https://github.com/acme/repo",
            labels=["documentation"],
        )
        decision = should_fetch_github_context(jira)
        assert decision.should_fetch is False
        assert "Non-code" in decision.reason

    def test_url_in_description(self):
        jira = _make_jira(description="Repo: https://github.com/acme/backend")
        decision = should_fetch_github_context(jira)
        assert decision.should_fetch is True
        assert decision.repo_url == "https://github.com/acme/backend"
        assert decision.source == "jira_description"

    def test_url_in_assignee_comment(self):
        jira = _make_jira(
            description="No URL here",
            assignee_account_id="dev1",
            comments=[
                {
                    "author": "Dev",
                    "account_id": "dev1",
                    "created": "2024-01-15",
                    "body": "Use https://github.com/acme/service",
                },
            ],
        )
        decision = should_fetch_github_context(jira)
        assert decision.should_fetch is True
        assert decision.repo_url == "https://github.com/acme/service"
        assert decision.source == "assignee_comment"

    def test_url_in_custom_field(self):
        jira = _make_jira(
            project_link="https://github.com/acme/infra",
        )
        decision = should_fetch_github_context(jira)
        assert decision.should_fetch is True
        assert decision.repo_url == "https://github.com/acme/infra"
        assert decision.source == "custom_field"

    def test_custom_field_non_github_ignored(self):
        """Confluence link in project_link should not trigger GitHub fetch."""
        jira = _make_jira(
            project_link="https://confluence.example.com/pages/123",
        )
        decision = should_fetch_github_context(jira)
        assert decision.should_fetch is False
        assert "No GitHub repository URL" in decision.reason

    def test_url_in_confluence_passport(self):
        jira = _make_jira()
        confluence = _make_confluence(passport_content="Repository: https://github.com/acme/mono")
        decision = should_fetch_github_context(jira, refined_confluence=confluence)
        assert decision.should_fetch is True
        assert decision.repo_url == "https://github.com/acme/mono"
        assert decision.source == "confluence_passport"

    def test_no_url_anywhere_skips(self):
        jira = _make_jira(description="Just a plain task description")
        decision = should_fetch_github_context(jira)
        assert decision.should_fetch is False
        assert "No GitHub repository URL" in decision.reason

    def test_description_url_takes_priority_over_passport(self):
        jira = _make_jira(description="https://github.com/acme/task-repo")
        confluence = _make_confluence(passport_content="https://github.com/acme/project-repo")
        decision = should_fetch_github_context(jira, refined_confluence=confluence)
        assert decision.should_fetch is True
        assert decision.repo_url == "https://github.com/acme/task-repo"
        assert decision.source == "jira_description"

    def test_feature_type_process_skips_even_with_url(self):
        jira = _make_jira(description="https://github.com/acme/repo")
        decision = should_fetch_github_context(jira, feature_type="process")
        assert decision.should_fetch is False
        assert "Non-code" in decision.reason

    def test_feature_type_documentation_only_skips(self):
        jira = _make_jira(description="https://github.com/acme/repo")
        decision = should_fetch_github_context(jira, feature_type="documentation_only")
        assert decision.should_fetch is False

    def test_comment_url_priority_over_custom_field(self):
        """Assignee comment URL should take priority over custom field."""
        jira = _make_jira(
            assignee_account_id="dev1",
            comments=[
                {
                    "author": "Dev",
                    "account_id": "dev1",
                    "created": "2024-01-15",
                    "body": "Use https://github.com/acme/from-comment",
                },
            ],
            project_link="https://github.com/acme/from-field",
        )
        decision = should_fetch_github_context(jira)
        assert decision.should_fetch is True
        assert decision.repo_url == "https://github.com/acme/from-comment"
        assert decision.source == "assignee_comment"

    def test_no_confluence_still_works(self):
        """Gate works without Confluence context."""
        jira = _make_jira(description="Repo: https://github.com/acme/solo")
        decision = should_fetch_github_context(jira, refined_confluence=None)
        assert decision.should_fetch is True
        assert decision.source == "jira_description"


# =============================================================================
# TestGitHubContextSkippedState
# =============================================================================


class TestGitHubContextSkippedState:
    """Tests for GitHubContext with SKIPPED status."""

    def test_skipped_format_markdown(self):
        ctx = GitHubContext(status=RepoStatus.SKIPPED, skip_reason="Non-code task")
        md = ctx.format_markdown()
        assert "Skipped" in md
        assert "Non-code task" in md

    def test_skipped_format_markdown_no_reason(self):
        ctx = GitHubContext(status=RepoStatus.SKIPPED)
        md = ctx.format_markdown()
        assert "not required for this task" in md

    def test_skipped_is_not_available(self):
        ctx = GitHubContext(status=RepoStatus.SKIPPED)
        assert ctx.is_available() is False

    def test_skipped_to_json_includes_status(self):
        ctx = GitHubContext(status=RepoStatus.SKIPPED, skip_reason="test reason")
        data = ctx.to_json()
        assert data["meta"]["status"] == "skipped"
        assert data["meta"]["skip_reason"] == "test reason"

    def test_to_json_includes_override_urls(self):
        ctx = GitHubContext(
            status=RepoStatus.EXISTS,
            task_repo_url="https://github.com/acme/task",
            project_repo_url="https://github.com/acme/project",
        )
        data = ctx.to_json()
        assert data["meta"]["task_repo_url"] == "https://github.com/acme/task"
        assert data["meta"]["project_repo_url"] == "https://github.com/acme/project"


# =============================================================================
# TestGitHubFetchDecision
# =============================================================================


class TestGitHubFetchDecision:
    """Tests for GitHubFetchDecision dataclass."""

    def test_skip_decision(self):
        d = GitHubFetchDecision(should_fetch=False, reason="No URL")
        assert d.should_fetch is False
        assert d.repo_url is None
        assert d.source == "none"

    def test_fetch_decision(self):
        d = GitHubFetchDecision(
            should_fetch=True,
            reason="Found in description",
            repo_url="https://github.com/acme/repo",
            source="jira_description",
        )
        assert d.should_fetch is True
        assert d.repo_url == "https://github.com/acme/repo"
        assert d.source == "jira_description"
