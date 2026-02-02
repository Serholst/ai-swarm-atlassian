"""Rate limiting utilities for API calls.

Implements token bucket algorithm with exponential backoff.
"""

import time
import asyncio
import logging
from typing import TypeVar, Callable, Awaitable
from functools import wraps

logger = logging.getLogger(__name__)

T = TypeVar("T")


class RateLimiter:
    """Token bucket rate limiter with exponential backoff."""

    def __init__(
        self,
        requests_per_second: float = 10.0,
        burst_size: int = 20,
        max_retries: int = 3,
        base_delay: float = 1.0,
    ):
        """
        Initialize rate limiter.

        Args:
            requests_per_second: Sustained request rate
            burst_size: Maximum burst capacity
            max_retries: Max retry attempts on 429
            base_delay: Base delay for exponential backoff
        """
        self.requests_per_second = requests_per_second
        self.burst_size = burst_size
        self.max_retries = max_retries
        self.base_delay = base_delay

        self._tokens = float(burst_size)
        self._last_update = time.monotonic()
        self._lock = asyncio.Lock() if asyncio.get_event_loop_policy() else None

    def _refill_tokens(self) -> None:
        """Refill tokens based on elapsed time."""
        now = time.monotonic()
        elapsed = now - self._last_update
        self._tokens = min(
            self.burst_size,
            self._tokens + elapsed * self.requests_per_second
        )
        self._last_update = now

    def acquire_sync(self) -> None:
        """Acquire a token synchronously, blocking if necessary."""
        self._refill_tokens()

        if self._tokens < 1.0:
            wait_time = (1.0 - self._tokens) / self.requests_per_second
            logger.debug(f"Rate limit: waiting {wait_time:.2f}s")
            time.sleep(wait_time)
            self._refill_tokens()

        self._tokens -= 1.0

    async def acquire_async(self) -> None:
        """Acquire a token asynchronously."""
        async with self._lock:
            self._refill_tokens()

            if self._tokens < 1.0:
                wait_time = (1.0 - self._tokens) / self.requests_per_second
                logger.debug(f"Rate limit: waiting {wait_time:.2f}s")
                await asyncio.sleep(wait_time)
                self._refill_tokens()

            self._tokens -= 1.0

    def get_backoff_delay(self, attempt: int) -> float:
        """Calculate exponential backoff delay."""
        return self.base_delay * (2 ** attempt)


class APIRateLimiter:
    """Rate limiter specifically for Atlassian API calls."""

    # Atlassian rate limits (conservative estimates)
    JIRA_RATE = 10.0  # requests per second
    CONFLUENCE_RATE = 10.0
    BURST_SIZE = 20

    _jira_limiter: RateLimiter | None = None
    _confluence_limiter: RateLimiter | None = None

    @classmethod
    def get_jira_limiter(cls) -> RateLimiter:
        """Get or create Jira rate limiter (singleton)."""
        if cls._jira_limiter is None:
            cls._jira_limiter = RateLimiter(
                requests_per_second=cls.JIRA_RATE,
                burst_size=cls.BURST_SIZE,
            )
        return cls._jira_limiter

    @classmethod
    def get_confluence_limiter(cls) -> RateLimiter:
        """Get or create Confluence rate limiter (singleton)."""
        if cls._confluence_limiter is None:
            cls._confluence_limiter = RateLimiter(
                requests_per_second=cls.CONFLUENCE_RATE,
                burst_size=cls.BURST_SIZE,
            )
        return cls._confluence_limiter


def rate_limited(limiter: RateLimiter):
    """Decorator for rate-limited synchronous functions."""

    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @wraps(func)
        def wrapper(*args, **kwargs) -> T:
            limiter.acquire_sync()
            return func(*args, **kwargs)
        return wrapper

    return decorator


def rate_limited_async(limiter: RateLimiter):
    """Decorator for rate-limited async functions."""

    def decorator(func: Callable[..., Awaitable[T]]) -> Callable[..., Awaitable[T]]:
        @wraps(func)
        async def wrapper(*args, **kwargs) -> T:
            await limiter.acquire_async()
            return await func(*args, **kwargs)
        return wrapper

    return decorator


def with_retry(
    max_retries: int = 3,
    base_delay: float = 1.0,
    retry_on: tuple = (429, 503),
):
    """Decorator for retrying failed requests with exponential backoff."""

    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @wraps(func)
        def wrapper(*args, **kwargs) -> T:
            last_exception = None

            for attempt in range(max_retries + 1):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    last_exception = e

                    # Check if we should retry
                    status_code = getattr(getattr(e, 'response', None), 'status_code', None)
                    if status_code not in retry_on:
                        raise

                    if attempt < max_retries:
                        delay = base_delay * (2 ** attempt)
                        logger.warning(
                            f"Request failed with {status_code}, "
                            f"retrying in {delay:.1f}s (attempt {attempt + 1}/{max_retries})"
                        )
                        time.sleep(delay)

            raise last_exception

        return wrapper

    return decorator
