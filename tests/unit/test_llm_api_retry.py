"""Unit tests for LLM API retry with exponential backoff (Gap 4)."""

from dataclasses import dataclass
from unittest.mock import MagicMock, patch, call
import pytest

from src.executor.phases.llm_executor import LLMExecutor


class FakeAPIError(Exception):
    """Simulates an OpenAI API error with status_code attribute."""

    def __init__(self, status_code: int, message: str = "API Error"):
        self.status_code = status_code
        super().__init__(message)


@dataclass
class FakeUsage:
    prompt_tokens: int = 10
    completion_tokens: int = 20


@dataclass
class FakeChoice:
    message: MagicMock = None
    finish_reason: str = "stop"

    def __post_init__(self):
        if self.message is None:
            self.message = MagicMock(content="test response")


@dataclass
class FakeCompletion:
    choices: list = None
    usage: FakeUsage = None

    def __post_init__(self):
        if self.choices is None:
            self.choices = [FakeChoice()]
        if self.usage is None:
            self.usage = FakeUsage()


@pytest.fixture
def executor():
    """Create LLMExecutor with mocked client."""
    with patch("src.executor.phases.llm_executor.OpenAI") as mock_openai:
        mock_client = MagicMock()
        mock_openai.return_value = mock_client
        exec_ = LLMExecutor(api_key="test-key", model="test-model")
        exec_.client = mock_client
        return exec_


class TestCallApiWithRetry:
    """Tests for _call_api_with_retry method."""

    def test_success_on_first_try(self, executor):
        """Successful API call should return immediately."""
        executor.client.chat.completions.create.return_value = FakeCompletion()
        result = executor._call_api_with_retry(
            messages=[{"role": "user", "content": "test"}],
            model="test-model",
        )
        assert result is not None
        assert executor.client.chat.completions.create.call_count == 1

    @patch("src.executor.phases.llm_executor.time.sleep")
    def test_retry_on_429(self, mock_sleep, executor):
        """429 should trigger retry with backoff."""
        executor.client.chat.completions.create.side_effect = [
            FakeAPIError(429, "Rate limited"),
            FakeCompletion(),
        ]
        result = executor._call_api_with_retry(
            messages=[{"role": "user", "content": "test"}],
            model="test-model",
        )
        assert result is not None
        assert executor.client.chat.completions.create.call_count == 2
        mock_sleep.assert_called_once_with(2)  # Base backoff = 2s

    @patch("src.executor.phases.llm_executor.time.sleep")
    def test_retry_on_503(self, mock_sleep, executor):
        """503 should trigger retry."""
        executor.client.chat.completions.create.side_effect = [
            FakeAPIError(503, "Service Unavailable"),
            FakeCompletion(),
        ]
        result = executor._call_api_with_retry(
            messages=[{"role": "user", "content": "test"}],
            model="test-model",
        )
        assert result is not None
        assert executor.client.chat.completions.create.call_count == 2

    @patch("src.executor.phases.llm_executor.time.sleep")
    def test_exponential_backoff_timing(self, mock_sleep, executor):
        """Backoff should double each retry: 2s, 4s, then fail."""
        executor.client.chat.completions.create.side_effect = [
            FakeAPIError(503),
            FakeAPIError(503),
            FakeAPIError(503),  # Max retries = 3, exhausted
        ]
        with pytest.raises(FakeAPIError):
            executor._call_api_with_retry(
                messages=[{"role": "user", "content": "test"}],
                model="test-model",
            )
        assert mock_sleep.call_args_list == [call(2), call(4)]
        assert executor.client.chat.completions.create.call_count == 3

    def test_no_retry_on_400(self, executor):
        """400 (bad request) should fail immediately without retry."""
        executor.client.chat.completions.create.side_effect = FakeAPIError(
            400, "Bad Request"
        )
        with pytest.raises(FakeAPIError, match="Bad Request"):
            executor._call_api_with_retry(
                messages=[{"role": "user", "content": "test"}],
                model="test-model",
            )
        assert executor.client.chat.completions.create.call_count == 1

    def test_no_retry_on_401(self, executor):
        """401 (auth error) should fail immediately."""
        executor.client.chat.completions.create.side_effect = FakeAPIError(
            401, "Unauthorized"
        )
        with pytest.raises(FakeAPIError, match="Unauthorized"):
            executor._call_api_with_retry(
                messages=[{"role": "user", "content": "test"}],
                model="test-model",
            )
        assert executor.client.chat.completions.create.call_count == 1

    @patch("src.executor.phases.llm_executor.time.sleep")
    def test_retry_on_502(self, mock_sleep, executor):
        """502 should trigger retry."""
        executor.client.chat.completions.create.side_effect = [
            FakeAPIError(502),
            FakeCompletion(),
        ]
        result = executor._call_api_with_retry(
            messages=[{"role": "user", "content": "test"}],
            model="test-model",
        )
        assert result is not None

    @patch("src.executor.phases.llm_executor.time.sleep")
    def test_retry_on_504(self, mock_sleep, executor):
        """504 should trigger retry."""
        executor.client.chat.completions.create.side_effect = [
            FakeAPIError(504),
            FakeCompletion(),
        ]
        result = executor._call_api_with_retry(
            messages=[{"role": "user", "content": "test"}],
            model="test-model",
        )
        assert result is not None

    def test_no_retry_when_no_status_code(self, executor):
        """Exception without status_code should not retry."""
        executor.client.chat.completions.create.side_effect = ConnectionError(
            "Connection refused"
        )
        with pytest.raises(ConnectionError):
            executor._call_api_with_retry(
                messages=[{"role": "user", "content": "test"}],
                model="test-model",
            )
        assert executor.client.chat.completions.create.call_count == 1


class TestCallLlm:
    """Tests for _call_llm using retry wrapper."""

    def test_call_llm_returns_parsed_response(self, executor):
        """_call_llm should return a fully parsed LLMResponse."""
        executor.client.chat.completions.create.return_value = FakeCompletion()
        response = executor._call_llm("test prompt")
        assert response.raw_content == "test response"
        assert response.tokens_used == 30  # 10 + 20
        assert response.model == "test-model"

    @patch("src.executor.phases.llm_executor.time.sleep")
    def test_call_llm_retries_transient(self, mock_sleep, executor):
        """_call_llm should retry on transient errors."""
        executor.client.chat.completions.create.side_effect = [
            FakeAPIError(429),
            FakeCompletion(),
        ]
        response = executor._call_llm("test prompt")
        assert response.raw_content == "test response"
        assert executor.client.chat.completions.create.call_count == 2
