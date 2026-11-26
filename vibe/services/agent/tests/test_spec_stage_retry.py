import asyncio
import json
from collections import Counter
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime
from types import SimpleNamespace

import httpx
import pytest
from langchain_core.messages import AIMessage
from openai import APITimeoutError, RateLimitError

from agent.services import spec_review as review
from agent.services import spec_stage_retry as retry
from agent.services.spec_document import Specification
from agent.services.spec_eval_checkpoint import RateLimitWaitingChat
from agent.spec_cli_progress import ProgressChat, ReviewProgress
from agent.utils.review_telemetry import review_run


def provider_status(code, *, body=None, delay=None):
    request = httpx.Request("POST", "https://provider.invalid")
    response = httpx.Response(code, request=request, headers={"retry-after": delay} if delay else {})
    if code == 429:
        return RateLimitError("synthetic", response=response, body=body)
    return httpx.HTTPStatusError("synthetic", request=request, response=response)


@pytest.mark.parametrize("status", [400, 401, 403, 404, 501, 505])
def test_permanent_provider_response_is_terminal(status):
    assert not retry.is_transient_provider_error(provider_status(status))


@pytest.mark.parametrize("status", [408, 429, 500, 502, 503, 504])
def test_transient_status_survives_wrapping(status):
    wrapper = review.ReviewProviderRateLimit("rate") if status == 429 else review.ReviewPipelineError("provider")
    wrapper.__cause__ = provider_status(status)
    assert retry.is_transient_provider_error(wrapper)


async def test_transport_retry_budget_covers_initial_and_corrected_response(monkeypatch):
    monkeypatch.setattr(retry, "TRANSIENT_RETRY_BACKOFF_SECONDS", 0)
    request = httpx.Request("POST", "https://provider.invalid")
    prompts = []

    async def attempt(prompt):
        prompts.append(prompt)
        if len(prompts) == 2:
            raise ValueError("Quote is absent from the cited source")
        raise APITimeoutError(request=request)

    with pytest.raises(APITimeoutError):
        await review.validated_retry("extraction", "prompt", attempt)
    assert prompts[:2] == ["prompt", "prompt"]
    assert len(prompts) == 3 and "CORRECTION:" in prompts[2]


async def test_extraction_retries_one_chunk_and_releases_gate_during_backoff(monkeypatch):
    lines = ["rule-" + str(index) + " " + "z" * 2600 for index in range(4)]
    spec = Specification("spec.md", "md", lines)
    chunks = Counter()
    entered_wait = asyncio.Event()
    sibling_ran = asyncio.Event()
    async def wait(delay):
        entered_wait.set()
        await sibling_ran.wait()
    monkeypatch.setattr(retry, "wait_backoff", wait)

    async def invoke(llm, stage, prompt, payload, output):
        key = payload["primary_start"]
        chunks[key] += 1
        if key == 1 and chunks[key] == 1:
            raise httpx.ReadTimeout("synthetic")
        if key == 2:
            assert entered_wait.is_set()
            sibling_ran.set()
        return output.model_validate({"requirements": [{"source_id": None, "text": "rule",
            "evidence": {"start": key, "end": key, "quote": lines[key-1][:6]}}]})

    monkeypatch.setattr(review, "invoke_stage", invoke)
    rows = await asyncio.wait_for(review.extract_requirements(spec, SimpleNamespace(), concurrency=1), timeout=1)
    assert [r.evidence.start for r in rows] == [1, 2, 3, 4]
    assert chunks == {1: 2, 2: 1, 3: 1, 4: 1}


async def test_rate_limit_retry_respects_retry_after_deadline_and_quota(monkeypatch):
    waited = []
    async def record(delay):
        waited.append(delay)
    monkeypatch.setattr(retry, "wait_backoff", record)
    attempts = 0
    failure = provider_status(429, delay="5")
    async def fail_then_recover(prompt):
        nonlocal attempts
        attempts += 1
        if attempts % 2 == 1:
            raise failure
        return 123

    now = asyncio.get_running_loop().time()
    assert await review.validated_retry("individual", "p", fail_then_recover, deadline=now+20) == 123
    assert waited == [5]
    with pytest.raises(RateLimitError):
        await review.validated_retry("individual", "p", fail_then_recover, deadline=now+2)
    assert attempts == 3 and waited == [5]
    attempts = 0
    failure = provider_status(429, delay=format_datetime(datetime.now(UTC) + timedelta(seconds=11), usegmt=True))
    assert await review.validated_retry("individual", "p", fail_then_recover, deadline=now+20) == 123
    assert 9 <= waited[-1] <= 11
    assert not retry.is_transient_provider_error(provider_status(429, body={"error": {"code": "insufficient_quota"}}))


async def test_backoff_cancelled_before_second_call(monkeypatch):
    entered = asyncio.Event()
    async def wait(delay):
        entered.set()
        await asyncio.Future()
    monkeypatch.setattr(retry, "wait_backoff", wait)
    calls = 0
    async def fail(prompt):
        nonlocal calls
        calls += 1
        raise httpx.ConnectTimeout("synthetic")

    task = asyncio.create_task(review.validated_retry("individual", "p", fail))
    await asyncio.wait_for(entered.wait(), timeout=1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert calls == 1


@pytest.mark.parametrize("sdk_retries,explicit_wait,expected", [(2, False, 1), (0, True, 2), (0, False, 2)])
async def test_harness_does_not_multiply_sdk_or_optional_cli_retry(sdk_retries, explicit_wait, expected, monkeypatch):
    async def no_wait(_):
        pass
    monkeypatch.setattr(retry, "wait_backoff", no_wait)
    calls = 0
    async def invoke(messages, **kwargs):
        nonlocal calls
        calls += 1
        raise provider_status(429)
    llm = SimpleNamespace(model_name="fixture", max_retries=sdk_retries, ainvoke=invoke)
    if explicit_wait:
        llm = RateLimitWaitingChat(llm, retry_limit=1, sleep=no_wait)
    llm = ProgressChat(llm, ReviewProgress(enabled=False))

    async def attempt(prompt):
        return await review.invoke_stage(llm, "extraction", prompt, {}, review.ExtractionDraft)

    with pytest.raises(review.ReviewProviderRateLimit):
        await review.validated_retry("extraction", "p", attempt)
    assert calls == expected


async def test_recovered_provider_call_counts_failed_and_successful_attempts(monkeypatch):
    monkeypatch.setattr(retry, "TRANSIENT_RETRY_BACKOFF_SECONDS", 0)
    calls = 0
    async def invoke(messages, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise APITimeoutError(request=httpx.Request("POST", "https://provider.invalid"))
        return AIMessage(content=json.dumps({"requirements": []}), usage_metadata={
            "input_tokens": 2, "output_tokens": 3, "total_tokens": 5,
        })

    llm = SimpleNamespace(model_name="fixture", max_retries=0, ainvoke=invoke)
    async def attempt(prompt):
        return await review.invoke_stage(llm, "extraction", prompt, {}, review.ExtractionDraft)

    with review_run(None, review_mode="legacy", source="cli") as run:
        assert not (await review.validated_retry("extraction", "p", attempt)).requirements
        usage = run.usage_summary()
    assert calls == usage["model_calls"] == 2
    assert usage["reported_input_tokens"] == 2 and usage["reported_output_tokens"] == 3
    assert not usage["usage_complete"]
