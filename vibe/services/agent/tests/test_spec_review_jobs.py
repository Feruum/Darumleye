"""Browser-independent review jobs still use the real review and archive pipeline."""

import asyncio
from datetime import UTC, datetime, timedelta
import json
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from langchain_openai import ChatOpenAI
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from sqlalchemy import update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from agent.config.settings import settings
from agent.infrastructure.database import Base, ReviewJobModel, get_session
from agent.routes import spec_review as review_route
from agent.server import app
from agent.utils.rate_limit import limiter
from agent.utils import review_telemetry
from test_spec_review import (
    HEADERS,
    TEXT,
    configure_provider,
    payloads,
    reference_manifest,
    reference_payload, REFERENCE_QUOTE,
)

JOBS = "/api/v1/spec-review-jobs"
ARCHIVE = "/api/v1/spec-reviews"


@pytest.fixture
async def job_sessions(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "api_key", HEADERS["X-API-Key"])
    monkeypatch.setattr(settings, "spec_review_use_nace", False)
    monkeypatch.setattr(settings, "upload_dir", str(tmp_path / "uploads"))
    limiter.reset()
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'jobs.sqlite'}")
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    async def session_dependency():
        async with sessions() as session:
            yield session

    monkeypatch.setitem(app.dependency_overrides, get_session, session_dependency)
    try:
        yield sessions
    finally:
        await review_route.stop_review_jobs(app)
        await engine.dispose()


@pytest.fixture
async def job_client(job_sessions):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client


def gated_provider(monkeypatch):
    calls = []
    configure_provider(monkeypatch, payloads(), calls)
    transport = app.state.agents["rag"].llm.http_async_client._transport
    original = transport.handle_async_request
    started, release = asyncio.Event(), asyncio.Event()

    async def handle(request):
        started.set()
        await release.wait()
        return await original(request)

    monkeypatch.setattr(transport, "handle_async_request", handle)
    return started, release, calls


async def submit(client, job_id, headers=HEADERS, text=TEXT):
    return await client.post(
        JOBS,
        headers=headers,
        data={"job_id": job_id, "review_mode": "legacy"},
        files={"file": ("requirements.txt", text.encode(), "text/plain")},
    )


async def wait_for_terminal(client, job_id):
    async with asyncio.timeout(10):
        while True:
            response = await client.get(f"{JOBS}/{job_id}", headers=HEADERS)
            assert response.status_code == 200, response.text
            if response.json()["status"] != "running":
                return response.json()
            await asyncio.sleep(0.01)


async def stored_job(sessions, job_id):
    async with sessions() as session:
        return await session.get(ReviewJobModel, job_id)


def test_review_upload_persists_selected_mode_and_restores_old_payload():
    upload = review_route.ReviewUpload("requirements.txt", "text/plain", b"REQ-1", [], [])
    metadata, primary, references = upload.storage()
    assert metadata["review_mode"] == "multi_agent"
    job = SimpleNamespace(payload_metadata=metadata, primary_bytes=primary, reference_bytes=references)
    assert review_route.ReviewUpload.restore(job).review_mode == "multi_agent"
    metadata.pop("review_mode")
    assert review_route.ReviewUpload.restore(job).review_mode == "legacy"
    metadata["review_mode"] = "unknown"
    with pytest.raises(review_route.ReviewJobFailure):
        review_route.ReviewUpload.restore(job)


async def test_multi_agent_job_reports_graph_and_first_admission_mode(job_client, monkeypatch):
    from test_spec_multi_agent import ExpertChat, SOURCE

    model = ExpertChat()
    monkeypatch.setattr(app.state, "agents", {"rag": SimpleNamespace(llm=model)}, raising=False)
    job_id = str(uuid4())
    async def submit_mode(mode):
        return await job_client.post(JOBS, headers=HEADERS, data={"job_id": job_id, **({"review_mode": mode} if mode else {})}, files={"file": ("requirements.txt", SOURCE.encode(), "text/plain")})

    assert (await submit_mode(None)).status_code == 202
    assert (await submit_mode("legacy")).status_code == 202  # Same UUID cannot replace admitted mode.
    terminal = await wait_for_terminal(job_client, job_id)
    assert terminal["status"] == "completed", terminal
    response = await job_client.get(f"{ARCHIVE}/{job_id}", headers=HEADERS)
    assert response.status_code == 200
    report = response.json()["report"]
    assert report["passport"]["review_mode"] == "multi_agent"
    assert report["evidence_graph"]["nodes"]
    assert report["passport"]["run_id"] == job_id
    assert response.json()["source_units"] == SOURCE.splitlines()
    other = {**HEADERS, "X-User-ID": "another-owner"}
    assert (await job_client.get(f"{ARCHIVE}/{job_id}", headers=other)).status_code == 404
    assert (await job_client.get(f"{JOBS}/{job_id}", headers=other)).status_code == 404


async def wait_for_slot_release(sessions, job_id):
    async with asyncio.timeout(5):
        while (job := await stored_job(sessions, job_id)).active_slot is not None:
            await asyncio.sleep(0.01)
        return job


def assert_terminal_payload_removed(job):
    assert job.primary_bytes is None
    assert job.reference_bytes is None
    assert job.payload_metadata is None
    assert job.active_slot is None and job.active_owner is None
    assert job.lease_token is None and job.lease_until is None


def restart_provider(monkeypatch, *, pause_after_extraction=False):
    """External model fixture returns the right stage even when one was cached."""
    replies = dict(zip(("extraction", "individual", "set"), payloads(), strict=True))
    calls, after_extraction = [], asyncio.Event()

    async def handle(request):
        body = json.loads(request.content)
        stage = json.loads(body["messages"][1]["content"])["stage"]
        calls.append(stage)
        if pause_after_extraction and stage != "extraction":
            after_extraction.set()
            await asyncio.Event().wait()
        return httpx.Response(
            200,
            json={
                "id": "restart-fixture",
                "object": "chat.completion",
                "created": 1,
                "model": "fixture",
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "stop",
                        "message": {
                            "role": "assistant",
                            "content": json.dumps(replies[stage], ensure_ascii=False),
                        },
                    }
                ],
            },
        )

    llm = ChatOpenAI(
        model="fixture",
        api_key="local-only",
        base_url="http://test-provider.invalid/v1",
        http_async_client=httpx.AsyncClient(transport=httpx.MockTransport(handle)),
    )
    monkeypatch.setattr(
        app.state, "agents", {"rag": SimpleNamespace(llm=llm)}, raising=False
    )
    return calls, after_extraction


def capture_resume_markers(monkeypatch):
    monkeypatch.delenv("OTEL_SDK_DISABLED", raising=False)
    provider = TracerProvider()
    memory = InMemorySpanExporter()
    provider.add_span_processor(SimpleSpanProcessor(memory))
    monkeypatch.setattr(review_telemetry, "_tracer", lambda: provider.get_tracer(review_telemetry.SCOPE))
    return memory


async def test_restart_resumes_same_durable_job_without_repeating_accepted_stage(
    job_client, job_sessions, monkeypatch
):
    telemetry = capture_resume_markers(monkeypatch)
    initial_calls, after_extraction = restart_provider(
        monkeypatch, pause_after_extraction=True
    )
    job_id = str(uuid4())
    assert (await submit(job_client, job_id)).status_code == 202
    await asyncio.wait_for(after_extraction.wait(), timeout=5)
    assert initial_calls.count("extraction") == 1
    assert not any(span.name == "checkpoint" for span in telemetry.get_finished_spans())

    # Graceful shutdown tears down the worker, not the committed job. A fresh
    # process uses the same database and upload volume, with no local state.
    await review_route.stop_review_jobs(app)
    resumed = await job_client.get(f"{JOBS}/{job_id}", headers=HEADERS)
    assert resumed.status_code == 200, resumed.text
    assert resumed.json()["status"] == "running"
    checkpoint_dir = Path(settings.upload_dir) / "spec-review-checkpoints" / job_id
    assert list(checkpoint_dir.glob("*.json"))
    paused = await stored_job(job_sessions, job_id)
    assert paused.status == "pending" and paused.lease_token is None
    assert paused.primary_bytes == TEXT.encode()
    assert 0 < paused.remaining_seconds <= 24 * 60
    monkeypatch.setattr(app.state, "agents", {})
    await review_route.start_review_jobs(app, job_sessions)
    assert (await stored_job(job_sessions, job_id)).status == "pending"
    assert (
        await stored_job(job_sessions, job_id)
    ).remaining_seconds == paused.remaining_seconds
    await review_route.stop_review_jobs(app)

    restarted_calls, _ = restart_provider(monkeypatch)
    await review_route.start_review_jobs(app, job_sessions)
    result = await wait_for_terminal(job_client, job_id)
    assert result["status"] == "completed", result
    assert result["review_id"] == job_id
    assert "extraction" not in restarted_calls
    assert "individual" in restarted_calls and "set" in restarted_calls
    markers = [span for span in telemetry.get_finished_spans() if span.name == "checkpoint"]
    assert len(markers) == 1
    assert markers[0].attributes["langfuse.session.id"] == job_id
    assert markers[0].attributes["langfuse.observation.metadata.source"] == "worker"
    assert markers[0].attributes["langfuse.observation.metadata.outcome"] == "completed"
    assert not markers[0].attributes.get("langfuse.observation.metadata.cache_hit")
    archived = await job_client.get(f"{ARCHIVE}/{job_id}", headers=HEADERS)
    assert archived.json()["source_units"] == TEXT.splitlines()
    assert archived.json()["report"]["passport"]["run_id"] == job_id
    await review_route.stop_review_jobs(app)
    assert not checkpoint_dir.exists()
    assert_terminal_payload_removed(await stored_job(job_sessions, job_id))


async def test_stale_lease_recovery_after_downtime_claims_once_and_reuses_checkpoint(
    job_client, job_sessions, monkeypatch
):
    telemetry = capture_resume_markers(monkeypatch)
    from agent.services.spec_review_jobs import LEASE_SECONDS, ReviewJobs

    initial_calls, after_extraction = restart_provider(
        monkeypatch, pause_after_extraction=True
    )
    job_id = str(uuid4())
    assert (await submit(job_client, job_id)).status_code == 202
    await asyncio.wait_for(after_extraction.wait(), timeout=5)
    assert initial_calls.count("extraction") == 1
    assert not any(span.name == "checkpoint" for span in telemetry.get_finished_spans())
    await review_route.stop_review_jobs(app)

    # Simulate a dead process leaving its last lease in the durable DB. Two
    # days of downtime must not consume the remaining active-time allowance.
    paused = await stored_job(job_sessions, job_id)
    crashed_at = datetime.now(UTC) - timedelta(days=2)
    async with job_sessions.begin() as session:
        await session.execute(
            update(ReviewJobModel)
            .where(ReviewJobModel.id == job_id)
            .values(
                status="running",
                lease_token=str(uuid4()),
                lease_until=crashed_at + timedelta(seconds=LEASE_SECONDS),
                deadline_at=crashed_at + timedelta(seconds=paused.remaining_seconds),
            )
        )
    restarted_calls, _ = restart_provider(monkeypatch)
    manager = await review_route.start_review_jobs(app, job_sessions)
    competing = ReviewJobs(
        job_sessions,
        manager.process,
        manager.checkpoint_root,
        provider_identity=manager.provider_identity,
    )
    competing.start()
    try:
        result = await wait_for_terminal(job_client, job_id)
        assert result["status"] == "completed", result
        assert restarted_calls.count("extraction") == 0
        assert restarted_calls.count("individual") == 1
        assert restarted_calls.count("set") == 1
        assert len([span for span in telemetry.get_finished_spans() if span.name == "checkpoint"]) == 1
        assert_terminal_payload_removed(await stored_job(job_sessions, job_id))
    finally:
        await competing.stop()


async def test_durable_upload_roundtrip_keeps_reference_order_and_bytes(
    job_client, job_sessions, monkeypatch
):
    started, _, _ = gated_provider(monkeypatch)
    job_id = str(uuid4())
    manifests = [
        reference_manifest(title="First reference"),
        reference_manifest(title="Second reference"),
    ]
    originals = [
        ("first.txt", "text/plain", b"First synthetic rule"),
        ("second.txt", "text/plain", b"Second synthetic rule"),
    ]
    response = await job_client.post(
        JOBS,
        headers=HEADERS,
        data={"job_id": job_id, "review_mode": "legacy", "reference_manifest": json.dumps(manifests)},
        files=[
            ("file", ("requirements.txt", TEXT.encode(), "text/plain")),
            *[
                ("reference_files", (name, content, mime))
                for name, mime, content in originals
            ],
        ],
    )
    assert response.status_code == 202, response.text
    await asyncio.wait_for(started.wait(), timeout=5)
    await review_route.stop_review_jobs(app)
    restored = review_route.ReviewUpload.restore(await stored_job(job_sessions, job_id))
    assert restored.content == TEXT.encode()
    assert restored.inputs == originals
    assert [entry.title for entry in restored.entries] == [
        "First reference",
        "Second reference",
    ]
    assert (await job_client.delete(f"{JOBS}/{job_id}", headers=HEADERS)).json()[
        "status"
    ] == "cancelled"
    assert_terminal_payload_removed(await stored_job(job_sessions, job_id))


async def test_cancellation_from_another_dispatcher_is_observed_and_persisted(
    job_client, job_sessions, monkeypatch
):
    from agent.services import spec_review_jobs

    monkeypatch.setattr(spec_review_jobs, "LEASE_REFRESH_SECONDS", 0.05)
    started, _, calls = gated_provider(monkeypatch)
    job_id = str(uuid4())
    assert (await submit(job_client, job_id)).status_code == 202
    await asyncio.wait_for(started.wait(), timeout=5)
    local = app.state.review_jobs
    remote = spec_review_jobs.ReviewJobs(
        job_sessions,
        local.process,
        local.checkpoint_root,
        provider_identity=local.provider_identity,
    )
    assert await remote.cancel("other-owner", job_id) is None
    cancelled = await remote.cancel(HEADERS["X-User-ID"], job_id)
    assert cancelled.status == "cancelled"
    assert_terminal_payload_removed(await wait_for_slot_release(job_sessions, job_id))
    await review_route.stop_review_jobs(app)
    assert (await job_client.get(f"{JOBS}/{job_id}", headers=HEADERS)).json()[
        "status"
    ] == "cancelled"
    assert (
        await job_client.get(f"{ARCHIVE}/{job_id}", headers=HEADERS)
    ).status_code == 404
    assert not calls


async def test_uploaded_review_continues_after_request_and_is_recoverable_from_archive(
    job_client, job_sessions, monkeypatch
):
    started, release, calls = gated_provider(monkeypatch)
    job_id = str(uuid4())
    response = await submit(job_client, job_id)
    assert response.status_code == 202, response.text
    assert response.json()["status"] == "running"
    assert response.json()["id"] == job_id
    assert response.headers["cache-control"] == "no-store"
    await asyncio.wait_for(started.wait(), timeout=5)

    # The upload request is over; a newly created browser client can poll.
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as reloaded:
        assert (await reloaded.get(f"{JOBS}/{job_id}", headers=HEADERS)).json()[
            "status"
        ] == "running"
        other = {**HEADERS, "X-User-ID": "another-owner"}
        assert (
            await reloaded.get(f"{JOBS}/{job_id}", headers=other)
        ).status_code == 404
        assert (
            await reloaded.delete(f"{JOBS}/{job_id}", headers=other)
        ).status_code == 404
        assert (await submit(reloaded, job_id)).json()["status"] == "running"
        assert (await submit(reloaded, str(uuid4()))).status_code == 429
        release.set()
        result = await wait_for_terminal(reloaded, job_id)
        assert result["status"] == "completed", result
        assert result["review_id"] == job_id
        report = await reloaded.get(f"{ARCHIVE}/{job_id}", headers=HEADERS)
        assert report.status_code == 200
        assert report.json()["report"]["passport"]["run_id"] == job_id
        assert report.json()["source_units"] == TEXT.splitlines()
        assert len(calls) > 0

    # Completed records remain discoverable even after the active-job registry
    # is gone (e.g. on service restart). Cancellation cannot delete an archive.
    manager = app.state.review_jobs
    await review_route.stop_review_jobs(app)
    terminal_job = await stored_job(job_sessions, job_id)
    await manager._cleanup_terminal(job_id, terminal_job.started_at, prune=True)
    assert await stored_job(job_sessions, job_id) is None
    assert (await job_client.get(f"{JOBS}/{job_id}", headers=HEADERS)).json()[
        "review_id"
    ] == job_id
    assert (await job_client.delete(f"{JOBS}/{job_id}", headers=HEADERS)).json()[
        "status"
    ] == "completed"
    other = {**HEADERS, "X-User-ID": "another-owner"}
    calls_before_retry = len(calls)
    assert (await submit(job_client, job_id)).json()["status"] == "completed"
    assert (await submit(job_client, job_id, headers=other)).status_code == 404
    assert len(calls) == calls_before_retry
    assert await stored_job(job_sessions, job_id) is None


async def test_old_terminal_cleanup_never_deletes_reused_uuid_checkpoint(
    job_sessions, tmp_path
):
    from agent.services.spec_review_jobs import ReviewJobs

    async def never_process(job):
        raise AssertionError("This test does not start workers")

    manager = ReviewJobs(
        job_sessions,
        never_process,
        tmp_path / "checkpoints",
        provider_identity="fixture",
    )
    identifier = str(uuid4())
    original = await manager.admit("owner", identifier, "old.txt", {}, b"old", b"")
    await manager.cancel("owner", identifier)
    await manager._cleanup_terminal(identifier, original.started_at, prune=True)
    replacement = await manager.admit("owner", identifier, "new.txt", {}, b"new", b"")
    assert replacement.started_at != original.started_at
    directory = manager.checkpoint_path(identifier)
    directory.mkdir(parents=True)
    marker = directory / "accepted.json"
    marker.write_text("synthetic checkpoint")
    await manager._cleanup_terminal(identifier, original.started_at, prune=True)
    assert marker.read_text() == "synthetic checkpoint"
    assert (await stored_job(job_sessions, identifier)).status == "pending"
    await manager.cancel("owner", identifier)


async def test_explicit_job_cancel_stops_provider_and_never_archives_partial_review(
    job_client, job_sessions, monkeypatch
):
    started, release, _ = gated_provider(monkeypatch)
    job_id = str(uuid4())
    assert (await submit(job_client, job_id)).status_code == 202
    await asyncio.wait_for(started.wait(), timeout=5)
    response = await job_client.delete(f"{JOBS}/{job_id}", headers=HEADERS)
    assert response.status_code == 200
    assert response.json()["status"] == "cancelled"
    release.set()
    assert (await wait_for_terminal(job_client, job_id))["status"] == "cancelled"
    assert (
        await job_client.get(f"{ARCHIVE}/{job_id}", headers=HEADERS)
    ).status_code == 404
    assert_terminal_payload_removed(await wait_for_slot_release(job_sessions, job_id))
    await review_route.stop_review_jobs(app)
    assert (await job_client.get(f"{JOBS}/{job_id}", headers=HEADERS)).json()[
        "status"
    ] == "cancelled"


async def test_invalid_uploaded_document_is_visible_as_terminal_job_error(
    job_client, job_sessions, monkeypatch
):
    configure_provider(monkeypatch, payloads(), [])
    job_id = str(uuid4())
    assert (await submit(job_client, job_id, text=" ")).status_code == 202
    result = await wait_for_terminal(job_client, job_id)
    assert result["status"] == "failed"
    assert result["error"]["code"] == "REVIEW_NO_TEXT"
    assert (
        await job_client.get(f"{ARCHIVE}/{job_id}", headers=HEADERS)
    ).status_code == 404
    assert_terminal_payload_removed(await stored_job(job_sessions, job_id))


async def test_cancelled_worker_still_occupies_a_slot_until_actual_work_exits(
    job_sessions, tmp_path
):
    from agent.services.spec_review_jobs import ReviewJobCapacityError, ReviewJobs

    started, draining, release = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def worker(job):
        if job.owner_id == "owner-a":
            started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                draining.set()
                await release.wait()
                raise
        else:
            await asyncio.Event().wait()

    jobs = ReviewJobs(
        job_sessions, worker, tmp_path / "checkpoints", provider_identity="fixture"
    )
    first = await jobs.admit("owner-a", str(uuid4()), "a.pdf", {}, b"a", b"")
    await jobs.admit("owner-b", str(uuid4()), "b.pdf", {}, b"b", b"")
    jobs.start()
    await asyncio.wait_for(started.wait(), timeout=5)
    try:
        await jobs.cancel("owner-a", first.id)
        await asyncio.wait_for(draining.wait(), timeout=5)
        with pytest.raises(ReviewJobCapacityError):
            await jobs.admit("owner-c", str(uuid4()), "c.pdf", {}, b"c", b"")
        release.set()
        assert_terminal_payload_removed(
            await wait_for_slot_release(job_sessions, first.id)
        )
        assert (
            await jobs.admit("owner-c", str(uuid4()), "c.pdf", {}, b"c", b"")
        ).status == "pending"
    finally:
        release.set()
        await jobs.stop()


async def test_active_job_has_a_bounded_total_deadline(
    job_sessions, tmp_path, monkeypatch
):
    from agent.services import spec_review_jobs

    monkeypatch.setattr(spec_review_jobs, "MAX_REVIEW_JOB_SECONDS", 0.01)

    async def worker(job):
        await asyncio.Event().wait()

    jobs = spec_review_jobs.ReviewJobs(
        job_sessions, worker, tmp_path / "checkpoints", provider_identity="fixture"
    )
    job = await jobs.admit("owner", str(uuid4()), "slow.pdf", {}, b"a", b"")
    jobs.start()
    try:
        terminal = await wait_for_slot_release(job_sessions, job.id)
        assert terminal.status == "failed"
        assert terminal.error_code == "REVIEW_FAILED"
        assert_terminal_payload_removed(terminal)
    finally:
        await jobs.stop()


async def test_durable_completion_archives_full_reference_and_reopens_exact_legacy_original(
    job_client, job_sessions, monkeypatch
):
    from agent.services.review_archive import CompletedReview
    import chromadb
    from chromadb.config import Settings as ChromaSettings

    monkeypatch.setattr(review_route, "get_chroma_client",
                        lambda: chromadb.EphemeralClient(ChromaSettings(anonymized_telemetry=False)))
    calls = []
    configure_provider(monkeypatch, payloads() + [reference_payload()], calls)
    job_id = str(uuid4())
    original = (REFERENCE_QUOTE + "\nContext outside the citation.").encode()
    response = await job_client.post(
        JOBS, headers=HEADERS, data={"job_id": job_id, "review_mode": "legacy", "reference_manifest": json.dumps([reference_manifest()])},
        files=[("file", ("requirements.txt", TEXT.encode(), "text/plain")), ("reference_files", ("rules.txt", original, "text/plain"))],
    )
    assert response.status_code == 202, response.text
    await review_route.start_review_jobs(app, job_sessions)
    terminal = await wait_for_terminal(job_client, job_id)
    assert terminal["status"] == "completed", terminal
    assert_terminal_payload_removed(await stored_job(job_sessions, job_id))
    detail = await job_client.get(f"{ARCHIVE}/{job_id}", headers=HEADERS)
    assert detail.status_code == 200
    assert "reference_source_units" not in detail.json()
    assert detail.json()["report"]["reference_review"]["observations"][0]["reference_evidence"][0]["quote"] == REFERENCE_QUOTE
    calls_after_review = len(calls)
    assert calls_after_review == 4
    path = f"{ARCHIVE}/{job_id}/references/S-001"
    result = await job_client.get(path, headers=HEADERS)
    assert result.status_code == 200, result.text
    snapshot = result.json()
    assert snapshot["source_units"] == original.decode().splitlines()
    assert result.headers["cache-control"] == "no-store"
    other = {**HEADERS, "X-User-ID": "another-owner"}
    assert (await job_client.get(path, headers=other)).status_code == 404
    # Simulate a reference report saved before the snapshot migration.
    async with job_sessions.begin() as session:
        await session.execute(update(CompletedReview).where(CompletedReview.id == job_id).values(reference_source_units=None))
    assert (await job_client.get(path, headers=HEADERS)).status_code == 404
    def upload(value):
        return {"file": ("same-name.txt", value, "text/plain")}
    assert (await job_client.post(path, headers=other, files=upload(original))).status_code == 404
    assert (await job_client.post(path, headers=HEADERS, files=upload(b"Wrong document"))).status_code == 422
    restored = await job_client.post(path, headers=HEADERS, files=upload(original))
    assert restored.status_code == 200, restored.text
    assert restored.json() == snapshot
    assert restored.headers["cache-control"] == "no-store"
    # Request-local restoration never mutates history or invokes more AI.
    assert (await job_client.get(path, headers=HEADERS)).status_code == 404
    assert len(calls) == calls_after_review
