"""Exercise task-local provider recovery through the real LangGraph fan-out."""
import asyncio
import json
from collections import Counter
from uuid import uuid4

import httpx
import pytest

from agent.services import spec_stage_retry
from agent.services.spec_eval_checkpoint import StageCheckpoint, checkpoint_scope
from agent.services.spec_review import ReviewPipelineError, review_specification
from agent.utils.review_telemetry import review_run
from test_spec_multi_agent import ExpertChat, source


FAILS = "multi_logic_inspector_individual"
SIBLING = "multi_scenario_builder_set"


class TimedOutExpert(ExpertChat):
    def __init__(self, failures, *, slow_sibling=False):
        super().__init__()
        self.failures = failures
        self.slow_sibling = slow_sibling
        self.attempts = []
        self.exhausted = asyncio.Event()
        self.sibling_completed = False
        self.inflight = 0
        self.max_inflight = 0

    async def ainvoke(self, messages, **kwargs):
        stage = json.loads(messages[-1].content)["stage"]
        self.attempts.append(stage)
        self.inflight += 1
        self.max_inflight = max(self.max_inflight, self.inflight)
        try:
            if stage == FAILS and self.attempts.count(stage) <= self.failures:
                if self.attempts.count(stage) == 2:
                    self.exhausted.set()
                raise httpx.ReadTimeout("fixture timeout")
            if stage == SIBLING and self.slow_sibling:
                await self.exhausted.wait()
                await asyncio.sleep(.02)
            message = await super().ainvoke(messages, **kwargs)
            if stage == SIBLING:
                self.sibling_completed = True
            return message
        finally:
            self.inflight -= 1


@pytest.mark.parametrize("mode", ["multi_agent", "multi_agent_direct"])
async def test_expert_retries_only_failed_task_outside_the_single_slot(monkeypatch, mode):
    chat = TimedOutExpert(1)
    sibling_called = asyncio.Event()
    invoke = chat.ainvoke

    async def observe(messages, **kwargs):
        if json.loads(messages[-1].content)["stage"] == SIBLING:
            sibling_called.set()
        return await invoke(messages, **kwargs)

    chat.ainvoke = observe

    async def wait_for_other_expert(_delay):
        # With the only slot held during backoff this cannot complete.
        async with asyncio.timeout(2):
            await sibling_called.wait()

    monkeypatch.setattr(spec_stage_retry, "wait_backoff", wait_for_other_expert)
    run_id = str(uuid4())
    with review_run(run_id, review_mode=mode) as trace:
        report = await review_specification(source(), chat, run_id=run_id, review_mode=mode, concurrency=1)
        usage = trace.usage_summary()
    stages = Counter(chat.attempts)
    assert stages[FAILS] == 2
    assert stages[SIBLING] == 1
    assert all(n == 1 for stage, n in stages.items() if stage.endswith(("_individual", "_set")) and stage != FAILS)
    assert report.passport.review_mode == mode
    assert chat.max_inflight == 1
    assert usage["model_calls"] == len(chat.attempts)
    assert not usage["usage_complete"]


@pytest.mark.parametrize("mode", ["multi_agent", "multi_agent_direct"])
async def test_exhausted_task_waits_for_other_experts_and_resumes_only_missing_coverage(tmp_path, monkeypatch, mode):
    monkeypatch.setattr(spec_stage_retry, "TRANSIENT_RETRY_BACKOFF_SECONDS", .001)
    checkpoint = StageCheckpoint(tmp_path, provider_identity="fixture")
    chat = TimedOutExpert(2, slow_sibling=True)
    run_id = str(uuid4())
    with checkpoint_scope(checkpoint), pytest.raises(ReviewPipelineError, match="logic_inspector:individual"):
        await review_specification(source(), chat, run_id=run_id, review_mode=mode, concurrency=3)

    assert chat.attempts.count(FAILS) == 2
    assert chat.sibling_completed
    assert chat.max_inflight <= 3
    assert chat.inflight == 0
    assert "multi_finalizer" not in chat.attempts
    assert all(not stage.startswith("multi_pair_") for stage in chat.attempts)

    recovered = TimedOutExpert(0)
    with checkpoint_scope(checkpoint):
        report = await review_specification(source(), recovered, run_id=run_id, review_mode=mode, concurrency=3)
    assert report.passport.run_id == run_id
    assert [s for s in recovered.attempts if s.endswith(("_individual", "_set"))] == [FAILS]
    assert "extraction" not in recovered.attempts
    assert "multi_finalizer" in recovered.attempts


@pytest.mark.parametrize("stop", ["cancel", "deadline"])
async def test_expert_backoff_obeys_cancellation_and_shared_deadline(monkeypatch, stop):
    started = asyncio.Event()
    stopped = asyncio.Event()

    async def blocked(_delay):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()

    monkeypatch.setattr(spec_stage_retry, "wait_backoff", blocked)
    chat = TimedOutExpert(2)
    deadline = asyncio.get_running_loop().time() + (0.6 if stop == "deadline" else 10)
    if stop == "deadline":
        monkeypatch.setattr(spec_stage_retry, "TRANSIENT_RETRY_BACKOFF_SECONDS", .001)
    task = asyncio.create_task(review_specification(source(), chat, review_mode="multi_agent_direct", deadline=deadline))
    async with asyncio.timeout(2):
        await started.wait()
        if stop == "cancel":
            task.cancel()
        with pytest.raises(asyncio.CancelledError if stop == "cancel" else TimeoutError):
            await task
        await stopped.wait()

    assert chat.attempts.count(FAILS) == 1
    assert "multi_finalizer" not in chat.attempts
    assert chat.inflight == 0


async def test_invalid_terminal_leaf_preserves_independent_expert_checkpoint(tmp_path):
    invalid_leaf_replied = asyncio.Event()
    sibling_completed = asyncio.Event()

    class IncompleteChecks(ExpertChat):
        async def ainvoke(self, messages, **kwargs):
            request = json.loads(messages[-1].content)
            stage = request["stage"]
            ids = [r["id"] for r in request.get("requirements", ())]
            if stage == SIBLING:
                await asyncio.wait_for(invalid_leaf_replied.wait(), 2)
                await asyncio.sleep(.02)
                sibling_completed.set()
            response = await super().ainvoke(messages, **kwargs)
            if stage == FAILS and "R-002" in ids:
                reply = json.loads(response.content)
                reply["requirements"][-1]["checks"].pop()
                if ids == ["R-002"] and "\nCORRECTION:" in messages[0].content:
                    invalid_leaf_replied.set()
                return response.model_copy(update={"content": json.dumps(reply)})
            return response

    cache = StageCheckpoint(tmp_path, provider_identity="fixture")
    chat = IncompleteChecks()
    with checkpoint_scope(cache), pytest.raises(ReviewPipelineError, match="Missing, duplicate or unexpected review criteria") as result:
        await review_specification(source(), chat, review_mode="multi_agent_direct")
    assert "logic_inspector:individual:0000" in str(result.value)
    assert sibling_completed.is_set()
    assert all(r["stage"] != "multi_finalizer" for r in chat.stages)

    resumed = ExpertChat()
    with checkpoint_scope(cache):
        report = await review_specification(source(), resumed, review_mode="multi_agent_direct")
    assert report.passport.review_mode == "multi_agent_direct"
    experts = [(r["stage"], [req["id"] for req in r["requirements"]])
               for r in resumed.stages if r["stage"].endswith(("_individual", "_set"))]
    assert experts == [(FAILS, ["R-002"])]


@pytest.mark.parametrize("status", [401, 403])
async def test_permanent_provider_rejection_stops_without_task_retry(monkeypatch, status):
    from openai import APIStatusError

    class RejectedExpert(ExpertChat):
        async def ainvoke(self, messages, **kwargs):
            stage = json.loads(messages[-1].content)["stage"]
            if stage == FAILS:
                req = httpx.Request("POST", "https://fixture.invalid/")
                raise APIStatusError("private-message", response=httpx.Response(status, request=req), body=None)
            return await super().ainvoke(messages, **kwargs)

    async def no_backoff(_):
        pytest.fail("Permanent rejection must not be retried")

    monkeypatch.setattr(spec_stage_retry, "wait_backoff", no_backoff)
    with pytest.raises(ReviewPipelineError) as result:
        await review_specification(source(), RejectedExpert(), review_mode="multi_agent_direct")
    assert isinstance(result.value.__cause__, APIStatusError)
    assert result.value.__cause__.status_code == status
    assert "private-message" not in str(result.value)
