"""Authenticated upload endpoint for FinTech specification reviews."""

import asyncio
from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path
from typing import Annotated
from uuid import UUID

import structlog
from pydantic import BeforeValidator
from fastapi import APIRouter, Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agent.config.settings import settings
from agent.infrastructure.database import ReviewJobModel, async_session, get_session
from agent.memory.store import get_chroma_client
from agent.server_auth import UserProfile, get_current_user
from agent.services.spec_reference_context import configured_reference_retriever
from agent.services.spec_document import Specification, SpecificationValidationError
from agent.services.spec_fix import FixInput, propose_fix
from agent.services.review_archive import ReviewArchive, archive_specification, validate_archive_graph
from agent.services.spec_reference_review import (
    MAX_REFERENCE_FILE_BYTES,
    MAX_REFERENCE_DOCUMENT_CHARACTERS,
    MAX_REFERENCE_DOCUMENT_PAGES,
    MAX_REFERENCE_TOTAL_BYTES,
    ReferenceManifestEntry,
    parse_reference_manifest,
    prepare_references,
    reference_byte_limit,
)
from agent.services.spec_review_mode import REVIEW_MODES, ActiveReviewMode, ReviewMode, new_review_mode
from agent.services.spec_review_jobs import MAX_REVIEW_JOB_SECONDS, ReviewJobCapacityError, ReviewJobFailure, ReviewJobs, get_review_job, public_job
from agent.services.spec_review import (
    MAX_REVIEW_FILE_BYTES,
    ReviewReport,
    ReviewProviderRateLimit,
    extract_specification,
    review_specification_configured,
)
from agent.utils.rate_limit import limiter
from agent.utils.review_telemetry import review_run, review_stage

router = APIRouter(prefix="/api/v1", tags=["specification review"])
logger = structlog.get_logger(__name__)
NewReviewMode = Annotated[ActiveReviewMode, BeforeValidator(new_review_mode), Form()]


def review_jobs(app: FastAPI, sessions=async_session) -> ReviewJobs:
    if not hasattr(app.state, "review_jobs"):
        app.state.review_jobs = ReviewJobs(
            sessions,
            lambda job: execute_durable_review(app, job),
            Path(settings.upload_dir) / "spec-review-checkpoints",
            provider_identity=settings.llm_base_url,
        )
    return app.state.review_jobs


async def start_review_jobs(app: FastAPI, sessions=async_session) -> ReviewJobs:
    jobs = review_jobs(app, sessions)
    if getattr(app.state, "agents", {}).get("rag") is not None:
        jobs.start()
    else:
        await jobs.maintain_without_provider()
    return jobs


async def stop_review_jobs(app: FastAPI) -> None:
    if hasattr(app.state, "review_jobs"):
        try:
            await app.state.review_jobs.stop()
        finally:
            del app.state.review_jobs


def job_response(job: dict, *, status_code: int = 200) -> JSONResponse:
    return JSONResponse(job, status_code=status_code, headers={"Cache-Control": "no-store"})


async def archived_job(owner_id: str, job_id: str, session: AsyncSession) -> dict | None:
    record = await ReviewArchive(session).get(owner_id, job_id)
    if record is None:
        return None
    return {"id": job_id, "filename": record.filename, "started_at": record.report["passport"]["run_at"], "status": "completed", "review_id": record.id}


@dataclass(frozen=True)
class ReviewUpload:
    filename: str
    content_type: str
    content: bytes
    entries: list[ReferenceManifestEntry]
    inputs: list[tuple[str, str, bytes]]
    review_mode: ReviewMode = "multi_agent"

    def storage(self) -> tuple[dict, bytes, bytes]:
        names = [(self.filename, self.content_type), *((name, mime) for name, mime, _ in self.inputs)]
        if any(len(name.encode("utf-8")) > 4096 or len(mime.encode("utf-8")) > 255 for name, mime in names):
            raise SpecificationValidationError("INVALID_FILENAME", "Document metadata is too long")
        metadata = {
            "filename": self.filename,
            "content_type": self.content_type,
            "review_mode": self.review_mode,
            "entries": [entry.model_dump(mode="json") for entry in self.entries],
            "references": [{"filename": name, "content_type": mime, "size": len(content)} for name, mime, content in self.inputs],
        }
        return metadata, self.content, b"".join(content for _, _, content in self.inputs)

    @classmethod
    def restore(cls, job: ReviewJobModel) -> "ReviewUpload":
        try:
            data = job.payload_metadata
            primary, combined = job.primary_bytes, job.reference_bytes
            if not isinstance(primary, bytes) or not isinstance(combined, bytes) or len(primary) > MAX_REVIEW_FILE_BYTES or len(combined) > MAX_REFERENCE_TOTAL_BYTES:
                raise ValueError("Invalid stored input size")
            references = data["references"]
            entries = parse_reference_manifest(json.dumps(data["entries"], ensure_ascii=False, separators=(",", ":")), len(references))
            inputs, offset = [], 0
            for reference in references:
                size = reference["size"]
                if type(size) is not int or not 0 <= size <= MAX_REFERENCE_FILE_BYTES or offset + size > len(combined):
                    raise ValueError("Invalid stored reference size")
                inputs.append((reference["filename"], reference["content_type"], combined[offset:offset + size]))
                offset += size
            if offset != len(combined):
                raise ValueError("Invalid stored reference data")
            mode = data.get("review_mode", "legacy")
            if mode not in REVIEW_MODES:
                raise ValueError("Invalid stored review mode")
            return cls(data["filename"], data["content_type"], primary, entries, inputs, mode)
        except (ValueError, TypeError, KeyError, AttributeError) as error:
            raise ReviewJobFailure("REVIEW_INVALID_DOCUMENT", "The stored document could not be read") from error


@router.get("/spec-reviews")
async def list_spec_reviews(
    user: UserProfile = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    return JSONResponse({"reviews": await ReviewArchive(session).list(user.user_id)}, headers={"Cache-Control": "no-store"})


@router.get("/spec-reviews/{review_id}")
async def get_completed_spec_review(
    review_id: UUID,
    user: UserProfile = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    record = await ReviewArchive(session).get(user.user_id, str(review_id))
    if record is None:
        raise HTTPException(status_code=404, detail="Review not found")
    return JSONResponse(
        {"id": record.id, "report": record.report, "source_units": record.source_units},
        headers={"Cache-Control": "no-store"},
    )


@router.get("/spec-reviews/{review_id}/references/{source_id}")
async def get_completed_reference_source(
    review_id: UUID,
    source_id: str,
    user: UserProfile = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    if not source_id.startswith("S-") or not source_id[2:].isdigit() or not 3 <= len(source_id[2:]) <= 8:
        raise HTTPException(status_code=404, detail="Reference source not found")
    snapshot = await ReviewArchive(session).reference_source(user.user_id, str(review_id), source_id)
    if snapshot is None:
        raise HTTPException(status_code=404, detail="Reference source not found")
    return JSONResponse(snapshot, headers={"Cache-Control": "no-store"})


@router.post("/spec-reviews/{review_id}/references/{source_id}")
@limiter.limit(settings.rate_limit_per_user)
async def restore_completed_reference_source(
    request: Request,
    review_id: UUID,
    source_id: str,
    file: UploadFile = File(...),
    user: UserProfile = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    # Older archives can be viewed using the user's exact original file, not
    # arbitrary similarly named material. This is read-only and uses no LLM.
    record = await ReviewArchive(session).get(user.user_id, str(review_id))
    reference = (record.report.get("reference_review") or {}) if record else {}
    source = next((item for item in reference.get("sources", []) if item["id"] == source_id), None)
    if source is None:
        raise HTTPException(status_code=404, detail="Reference source not found")
    try:
        content = await file.read(MAX_REFERENCE_FILE_BYTES + 1)
    finally:
        await file.close()
    if len(content) > MAX_REFERENCE_FILE_BYTES:
        raise HTTPException(status_code=413, detail="Reference file is too large")
    if sha256(content).hexdigest() != source["sha256"]:
        raise HTTPException(status_code=422, detail="Reference file does not match the archived SHA-256")
    try:
        document = await run_in_threadpool(
            extract_specification, source["filename"], file.content_type or "application/octet-stream", content,
            max_file_bytes=MAX_REFERENCE_FILE_BYTES, max_characters=MAX_REFERENCE_DOCUMENT_CHARACTERS,
            max_pages=MAX_REFERENCE_DOCUMENT_PAGES, ocr_seconds=30,
        )
        if document.source != source["source"]:
            raise ValueError("Reference format mismatch")
        for observation in reference.get("observations", []):
            if observation["source_id"] == source_id:
                for evidence in observation["reference_evidence"]:
                    document.verify_quote(evidence["start"], evidence["end"], evidence["quote"])
        for node in (record.report.get("evidence_graph") or {}).get("nodes", []):
            if node["kind"] == "evidence" and node.get("source_id") == source_id and node.get("evidence"):
                evidence = node["evidence"]
                document.verify_quote(evidence["start"], evidence["end"], evidence["quote"])
    except (ValueError, SpecificationValidationError) as error:
        raise HTTPException(status_code=422, detail="Could not reproduce the archived reference citation locations") from error
    return JSONResponse({"source_id": source_id, "sha256": source["sha256"], "source_units": document.units},
                        headers={"Cache-Control": "no-store"})


@router.post("/spec-reviews/{review_id}/fix")
@limiter.limit(settings.rate_limit_per_user)
async def create_fix_proposal(
    request: Request,
    review_id: UUID,
    body: FixInput,
    user: UserProfile = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    record = await ReviewArchive(session).get(user.user_id, str(review_id))
    if record is None:
        raise HTTPException(status_code=404, detail="Review not found")
    report = ReviewReport.model_validate(record.report)
    index = int(body.finding_id[2:]) - 1
    if not 0 <= index < len(report.findings) or body.finding_id != f"D-{index + 1:03d}":
        raise HTTPException(status_code=404, detail="Finding not found")
    if report.unreadable_pages or report.unreadable_units:
        raise HTTPException(status_code=422, detail="Source contains unreadable pages or images")
    agent = request.app.state.agents.get("rag")
    if agent is None:
        raise HTTPException(status_code=503, detail={
            "code": "LLM_NOT_CONFIGURED",
            "message": "Configure a chat model to enable correction proposals",
        })
    specification = Specification(record.filename, report.source, record.source_units, ocr_pages=report.ocr_pages, ocr_units=report.ocr_units, unreadable_units=report.unreadable_units)
    try:
        async with asyncio.timeout(min(120, settings.spec_review_timeout_seconds)):
            proposal = await propose_fix(
                specification, report.findings[index], report.requirements,
                body.clarification.strip(), agent.llm,
            )
        return JSONResponse(proposal.model_dump(mode="json"), headers={"Cache-Control": "no-store"})
    except ReviewProviderRateLimit as error:
        raise HTTPException(status_code=429, detail={"code": "RATE_LIMITED", "message": "AI provider rate limit reached"}) from error
    except Exception as error:
        logger.warning("specification_fix_failed", error_type=type(error).__name__)
        raise HTTPException(status_code=502, detail={"code": "REVIEW_FAILED", "message": "Could not produce a valid correction proposal"}) from error


async def read_review_upload(
    file: UploadFile,
    reference_files: list[UploadFile] | None,
    reference_manifest: str | None,
    review_mode: ReviewMode = "multi_agent",
) -> ReviewUpload:
    content = await file.read(MAX_REVIEW_FILE_BYTES + 1)
    if len(content) > MAX_REVIEW_FILE_BYTES:
        raise HTTPException(
            status_code=413,
            detail={
                "code": "FILE_TOO_LARGE",
                "message": "File exceeds the 25 MiB review limit",
            },
        )
    try:
        uploads = reference_files or []
        entries = parse_reference_manifest(reference_manifest, len(uploads))
        inputs = []
        total = 0
        for upload in uploads:
            reference_content = await upload.read(MAX_REFERENCE_FILE_BYTES + 1)
            total += len(reference_content)
            if (
                len(reference_content) > MAX_REFERENCE_FILE_BYTES
                or total > MAX_REFERENCE_TOTAL_BYTES
            ):
                raise reference_byte_limit()
            inputs.append(
                (upload.filename or "", upload.content_type or "", reference_content)
            )
        return ReviewUpload(
            file.filename or "",
            file.content_type or "",
            content,
            entries,
            inputs,
            new_review_mode(review_mode),
        )
    except SpecificationValidationError as error:
        raise HTTPException(
            status_code=413 if error.code == "FILE_TOO_LARGE" else 422,
            detail={"code": error.code, "message": str(error)},
        ) from error


def prepare_review_upload(upload: ReviewUpload):
    specification = extract_specification(upload.filename, upload.content_type, upload.content)
    references = prepare_references(upload.entries, upload.inputs)
    return specification, references


async def prepare_job_upload(upload: ReviewUpload):
    # A native OCR worker cannot be interrupted safely from Python. Retain the
    # active job slot until it exits, even if the browser cancels the job.
    task = asyncio.create_task(run_in_threadpool(prepare_review_upload, upload))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        await asyncio.gather(task, return_exceptions=True)
        raise


def configured_agent(request: Request):
    agent = request.app.state.agents.get("rag")
    if agent is None:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "LLM_NOT_CONFIGURED",
                "message": "Configure a chat model to enable reviews",
            },
        )
    return agent


async def execute_review(upload: ReviewUpload, get_agent, owner_id: str, *, job_id: str | None = None, run_id: str | None = None):
    try:
        with review_stage("document-parsing"):
            if job_id is None:
                specification, references = await run_in_threadpool(prepare_review_upload, upload)
            else:
                specification, references = await prepare_job_upload(upload)
        report = await review_specification_configured(
            specification,
            get_agent().llm,
            settings,
            references=references,
            reference_retriever_factory=configured_reference_retriever(
                settings, owner_id, get_chroma_client
            ),
            run_id=run_id or job_id,
            review_mode=upload.review_mode,
        )
        validate_archive_graph(specification, report.model_dump(mode="json"))
        return archive_specification(specification, references), report
    except SpecificationValidationError as error:
        raise HTTPException(
            status_code=413 if error.code == "FILE_TOO_LARGE" else 422,
            detail={"code": error.code, "message": str(error)},
        ) from error
    except ReviewProviderRateLimit as error:
        raise HTTPException(
            status_code=429,
            detail={
                "code": "RATE_LIMITED",
                "message": "The configured AI provider reached its rate limit; retry later",
            },
        ) from error
    except HTTPException:
        raise
    except TimeoutError as error:
        raise HTTPException(
            status_code=502,
            detail={
                "code": "REVIEW_FAILED",
                "message": "The review exceeded its time limit; try a smaller document",
            },
        ) from error
    except Exception as error:
        logger.warning("specification_review_failed", error_type=type(error).__name__)
        raise HTTPException(
            status_code=502,
            detail={
                "code": "REVIEW_FAILED",
                "message": "The agent could not produce a valid review",
            },
        ) from error


@router.post("/spec-reviews")
@limiter.limit(settings.rate_limit_per_user)
async def create_spec_review(
    request: Request,
    file: UploadFile = File(...),
    reference_files: list[UploadFile] | None = File(default=None),
    reference_manifest: str | None = Form(default=None),
    review_mode: NewReviewMode = "multi_agent",
    user: UserProfile = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> ReviewReport:
    upload = await read_review_upload(file, reference_files, reference_manifest, review_mode)
    # Keep the original synchronous API and its validation order intact.
    with review_run(None, review_mode=upload.review_mode, source="api") as trace_run:
        specification, report = await execute_review(upload, lambda: configured_agent(request), user.user_id, run_id=trace_run.run_id)
        try:
            with review_stage("archive"):
                await ReviewArchive(session).save(user.user_id, specification, report)
            trace_run.report(report)
            trace_run.outcome("completed")
        except Exception as error:
            logger.warning("specification_archive_failed", error_type=type(error).__name__)
            raise HTTPException(status_code=502, detail={"code": "REVIEW_FAILED", "message": "The agent could not save the review"}) from error
    return report


async def execute_durable_review(app: FastAPI, job: ReviewJobModel):
    # A first claim retains the full admission allowance. Graceful requeue and
    # stale-lease recovery charge the previous attempt before calling us.
    # Emit exactly one checkpoint-entry marker for a charged-budget re-entry,
    # even if no individual model result was reusable.
    if job.remaining_seconds < MAX_REVIEW_JOB_SECONDS:
        with review_stage("checkpoint") as stage:
            stage.outcome("completed")
    upload = ReviewUpload.restore(job)
    agent = getattr(app.state, "agents", {}).get("rag")
    if agent is None:
        raise ReviewJobFailure("LLM_NOT_CONFIGURED", "Configure a chat model to enable reviews")
    try:
        return await execute_review(upload, lambda: agent, job.owner_id, job_id=job.id)
    except HTTPException as error:
        detail = error.detail if isinstance(error.detail, dict) else {}
        code = detail.get("code", "REVIEW_FAILED")
        message = detail.get("message", "The agent could not produce a valid review")
        if error.status_code == 413:
            code = "PAYLOAD_TOO_LARGE"
        elif error.status_code == 422 and code not in {"REVIEW_NO_TEXT", "REVIEW_TEXT_LIMIT"}:
            code, message = "REVIEW_INVALID_DOCUMENT", "The document could not be read"
        raise ReviewJobFailure(code, message) from error


@router.post("/spec-review-jobs", status_code=202)
@limiter.limit(settings.rate_limit_per_user)
async def create_spec_review_job(
    request: Request,
    job_id: UUID = Form(...),
    file: UploadFile = File(...),
    reference_files: list[UploadFile] | None = File(default=None),
    reference_manifest: str | None = Form(default=None),
    review_mode: NewReviewMode = "multi_agent",
    user: UserProfile = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    identifier = str(job_id)
    completed = await archived_job(user.user_id, identifier, session)
    if completed is not None:
        return job_response(completed, status_code=202)
    existing = await get_review_job(session, user.user_id, identifier)
    if existing is not None:
        return job_response(public_job(existing), status_code=202)
    upload = await read_review_upload(file, reference_files, reference_manifest, review_mode)
    configured_agent(request)
    try:
        metadata, primary, references = upload.storage()
    except SpecificationValidationError as error:
        raise HTTPException(status_code=422, detail={"code": error.code, "message": str(error)}) from error
    sessions = async_sessionmaker(session.bind, expire_on_commit=False)
    owner_id = user.user_id
    # Do not retain a read transaction while an independent worker transaction
    # commits the durable upload (SQLite tests and PostgreSQL alike).
    await session.rollback()
    jobs = await start_review_jobs(request.app, sessions)
    filename = Path(upload.filename.replace("\\", "/")).name[:255] or "document"
    try:
        job = await jobs.admit(owner_id, identifier, filename, metadata, primary, references)
    except PermissionError as error:
        raise HTTPException(status_code=404, detail="Review job not found") from error
    except ReviewJobCapacityError as error:
        raise HTTPException(status_code=429, detail={"code": "RATE_LIMITED", "message": str(error)}) from error
    return job_response(public_job(job), status_code=202)


@router.get("/spec-review-jobs/{job_id}")
async def get_spec_review_job(
    request: Request,
    job_id: UUID,
    user: UserProfile = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    identifier = str(job_id)
    job = await get_review_job(session, user.user_id, identifier)
    if job is not None:
        return job_response(public_job(job))
    # The durable archive is authoritative if the task was evicted, the
    # service restarted, or cancellation raced with a successful commit.
    completed = await archived_job(user.user_id, identifier, session)
    if completed is not None:
        return job_response(completed)
    raise HTTPException(status_code=404, detail="Review job not found")


@router.delete("/spec-review-jobs/{job_id}")
async def cancel_spec_review_job(
    request: Request,
    job_id: UUID,
    user: UserProfile = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    identifier = str(job_id)
    completed = await archived_job(user.user_id, identifier, session)
    if completed is not None:
        return job_response(completed)
    sessions = async_sessionmaker(session.bind, expire_on_commit=False)
    await session.rollback()
    jobs = review_jobs(request.app, sessions)
    job = await jobs.cancel(user.user_id, identifier)
    if job is None:
        raise HTTPException(status_code=404, detail="Review job not found")
    return job_response(public_job(job))
