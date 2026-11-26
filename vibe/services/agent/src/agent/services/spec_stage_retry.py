"""One bounded transport retry per logical review task, including its correction."""
import asyncio
from asyncio import sleep as wait_backoff
from collections.abc import Awaitable, Callable
from contextlib import nullcontext

import httpx
import structlog
from openai import APIConnectionError

from agent.services.spec_eval_checkpoint import RateLimitWaitingChat

logger = structlog.get_logger(__name__)
TRANSIENT_RETRY_LIMIT = 1
TRANSIENT_RETRY_BACKOFF_SECONDS = 1.0
TRANSIENT_HTTP_STATUSES = frozenset({408, 429, 500, 502, 503, 504})


class ReviewPipelineError(ValueError):
    """Safe review failure containing no provider or document data."""

    retry_managed_by_client = False


class ReviewProviderRateLimit(ReviewPipelineError):
    """The provider rejected a request due to rate or quota limits."""


def _retry_delay(error: Exception) -> float | None:
    cause = error.__cause__ if isinstance(error, ReviewPipelineError) else error
    status = getattr(cause, "status_code", None)
    if isinstance(cause, httpx.HTTPStatusError):
        status = cause.response.status_code
    if status in TRANSIENT_HTTP_STATUSES:
        return RateLimitWaitingChat.retry_delay(cause, default_delay=TRANSIENT_RETRY_BACKOFF_SECONDS)
    if isinstance(cause, (APIConnectionError, httpx.NetworkError, httpx.TimeoutException, httpx.RemoteProtocolError, ConnectionError)):
        return TRANSIENT_RETRY_BACKOFF_SECONDS
    return None


def is_transient_provider_error(error: Exception) -> bool:
    return _retry_delay(error) is not None


class StageRetry:
    def __init__(self, enabled: bool):
        self.remaining = TRANSIENT_RETRY_LIMIT if enabled else 0

    async def run[R](self, operation: Callable[[str], Awaitable[R]], prompt: str, *,
                     stage: str, gate: asyncio.Semaphore | None, deadline: float | None) -> R:
        loop = asyncio.get_running_loop()
        async with asyncio.timeout_at(deadline):
            while True:
                try:
                    async with gate if gate is not None else nullcontext():
                        if deadline is not None and loop.time() >= deadline:
                            raise TimeoutError
                        return await operation(prompt)
                except Exception as error:
                    if not self.remaining or getattr(error, "retry_managed_by_client", False):
                        raise
                    delay = _retry_delay(error)
                    if delay is None or (deadline is not None and loop.time() + delay >= deadline):
                        raise
                    self.remaining -= 1
                    logger.warning("spec_task_retry", stage=stage, retry=TRANSIENT_RETRY_LIMIT - self.remaining, seconds=delay)
                    await wait_backoff(delay)
