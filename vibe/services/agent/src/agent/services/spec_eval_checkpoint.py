"""Opt-in CLI checkpoint and provider cooldown for long evaluations."""

import asyncio
import json
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from hashlib import sha256
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any

import structlog
from openai import RateLimitError
from pydantic import BaseModel, ValidationError

logger = structlog.get_logger(__name__)


class StageCheckpoint:
    def __init__(self, directory: Path, *, provider_identity: str):
        self.directory = directory.resolve()
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.provider = sha256(provider_identity.encode()).hexdigest()

    def key(
        self,
        model: str,
        stage: str,
        instructions: str,
        data: dict,
        output: type[BaseModel],
        *,
        reasoning_effort: str | None = None,
        prompt_contract: str | None = None,
    ) -> str:
        # Source/prompt/provider/model/schema changes cannot reuse stale results.
        payload = [
            1,
            self.provider,
            model,
            stage,
            instructions,
            data,
            output.model_json_schema(),
        ]
        if reasoning_effort is not None:
            payload.append({"reasoning_effort": reasoning_effort})
        if prompt_contract is not None:
            payload.append({"prompt_contract_sha256":sha256(prompt_contract.encode()).hexdigest()})
        return sha256(
            json.dumps(
                payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            ).encode()
        ).hexdigest()

    def load[T: BaseModel](self, key: str, output: type[T]) -> T | None:
        try:
            return output.model_validate_json(
                (self.directory / f"{key}.json").read_bytes()
            )
        except (FileNotFoundError, ValidationError, OSError):
            return None

    def save(self, key: str, output: BaseModel) -> None:
        temporary = None
        try:
            with NamedTemporaryFile(
                dir=self.directory, mode="w", encoding="utf-8", delete=False
            ) as file:
                temporary = Path(file.name)
                file.write(output.model_dump_json())
            temporary.replace(self.directory / f"{key}.json")
        finally:
            if temporary:
                temporary.unlink(missing_ok=True)

    def discard(self, key: str) -> None:
        (self.directory / f"{key}.json").unlink(missing_ok=True)


@dataclass
class StageAttempt:
    checkpoint: StageCheckpoint | None
    base_instructions: str
    stage: str
    key: str | None = None
    result: BaseModel | None = None
    loaded: bool = False

    def accept(self) -> None:
        if self.checkpoint and self.key and self.result and not self.loaded:
            self.checkpoint.save(self.key, self.result)
            logger.info("spec_eval_checkpoint_saved", stage=self.stage)

    def reject(self) -> None:
        if self.checkpoint and self.key and self.loaded:
            self.checkpoint.discard(self.key)


_checkpoint: ContextVar[StageCheckpoint | None] = ContextVar(
    "spec_eval_checkpoint", default=None
)
_attempt: ContextVar[StageAttempt | None] = ContextVar(
    "spec_eval_stage_attempt", default=None
)


@contextmanager
def checkpoint_scope(checkpoint: StageCheckpoint | None) -> Iterator[None]:
    token = _checkpoint.set(checkpoint)
    try:
        yield
    finally:
        _checkpoint.reset(token)


@contextmanager
def checkpoint_attempt(stage: str, instructions: str) -> Iterator[StageAttempt]:
    attempt = StageAttempt(_checkpoint.get(), instructions, stage)
    token = _attempt.set(attempt)
    try:
        yield attempt
    finally:
        _attempt.reset(token)


def load_stage_checkpoint[T: BaseModel](
    model: str, stage: str, data: dict, output: type[T], *, reasoning_effort: str | None = None,
    prompt_contract: str | None = None,
) -> T | None:
    attempt = _attempt.get()
    if attempt is None or attempt.checkpoint is None:
        return None
    attempt.key = attempt.checkpoint.key(
        model, stage, attempt.base_instructions, data, output, reasoning_effort=reasoning_effort,
        prompt_contract=prompt_contract,
    )
    result = attempt.checkpoint.load(attempt.key, output)
    if result is not None:
        attempt.loaded = True
        attempt.result = result
        logger.info("spec_eval_checkpoint_reused", stage=stage)
    return result


def record_stage_checkpoint(result: BaseModel) -> None:
    attempt = _attempt.get()
    if attempt is not None and attempt.key is not None:
        attempt.result = result


class RateLimitWaitingChat:
    """CLI-only opt-in: respect Retry-After rather than rerun accepted batches."""

    def __init__(
        self,
        chat: Any,
        *,
        retry_limit: int = 3,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ):
        self.chat = chat
        self.model_name = chat.model_name
        self.retry_limit = retry_limit
        self.sleep = sleep

    @staticmethod
    def retry_delay(error: RateLimitError) -> float | None:
        body = error.body if isinstance(error.body, dict) else {}
        nested = body.get("error", body)
        code = getattr(error, "code", None) or (
            nested.get("code") if isinstance(nested, dict) else None
        )
        if code in {
            "insufficient_quota",
            "billing_hard_limit_reached",
            "billing_not_active",
        }:
            return None
        value = error.response.headers.get("retry-after")
        if value:
            try:
                delay = float(value)
            except ValueError:
                try:
                    delay = (
                        parsedate_to_datetime(value) - datetime.now(UTC)
                    ).total_seconds()
                except (ValueError, TypeError, OverflowError):
                    delay = 60
            # Never retry earlier than the provider requests. Very long waits
            # remain a recoverable checkpointed failure, not an endless loop.
            return max(1.0, delay) if 0 <= delay <= 300 else None
        return 60.0

    async def ainvoke(self, *args, **kwargs):
        for index in range(self.retry_limit + 1):
            try:
                return await self.chat.ainvoke(*args, **kwargs)
            except RateLimitError as error:
                delay = self.retry_delay(error)
                if index == self.retry_limit or delay is None:
                    raise
                logger.warning(
                    "spec_eval_rate_limit_wait", seconds=delay, retry=index + 1
                )
                await self.sleep(delay)
