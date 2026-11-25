"""Durable, bounded specification jobs; PostgreSQL owns admission and leases."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
import shutil
from time import monotonic
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import delete, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import defer
import structlog

from agent.infrastructure.database import ReviewJobModel
from agent.services.review_archive import CompletedReview, ReviewArchive
from agent.services.spec_eval_checkpoint import StageCheckpoint, checkpoint_scope
from agent.utils.review_telemetry import ReviewRun, review_run, review_stage

if TYPE_CHECKING:
    from agent.services.spec_document import Specification
    from agent.services.spec_review import ReviewReport

MAX_RUNNING_REVIEW_JOBS = 2
MAX_FINISHED_REVIEW_JOBS = 128
FINISHED_REVIEW_JOB_SECONDS = 3600
MAX_REVIEW_JOB_SECONDS = 24 * 60
LEASE_SECONDS = 90
LEASE_REFRESH_SECONDS = 10
DISPATCH_SECONDS = 1
ACTIVE_STATUSES = ("pending", "running")
TERMINAL_STATUSES = ("completed", "failed", "cancelled")
logger = structlog.get_logger(__name__)


class ReviewJobFailure(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


class ReviewJobCapacityError(Exception):
    pass


def utc(timestamp: datetime) -> datetime:
    return timestamp if timestamp.tzinfo is not None else timestamp.replace(tzinfo=UTC)


def public_job(job: ReviewJobModel) -> dict:
    status = "running" if job.status in ACTIVE_STATUSES else job.status
    result = {
        "id": job.id,
        "filename": job.filename,
        "started_at": utc(job.started_at).isoformat(),
        "status": status,
    }
    if status == "completed":
        result["review_id"] = job.id
    elif status == "failed":
        result["error"] = {"code": job.error_code, "message": job.error_message}
    return result


def metadata_query():
    # Polling must not load document binaries or reference metadata.
    return select(ReviewJobModel).options(
        defer(ReviewJobModel.primary_bytes),
        defer(ReviewJobModel.reference_bytes),
        defer(ReviewJobModel.payload_metadata),
    )


async def get_review_job(
    session: AsyncSession, owner_id: str, job_id: str
) -> ReviewJobModel | None:
    return await session.scalar(
        metadata_query().where(
            ReviewJobModel.id == job_id, ReviewJobModel.owner_id == owner_id
        )
    )


def terminal_values(status: str, *, error: ReviewJobFailure | None = None) -> dict:
    return {
        "status": status,
        "finished_at": datetime.now(UTC),
        "primary_bytes": None,
        "reference_bytes": None,
        "payload_metadata": None,
        "active_owner": None,
        "active_slot": None,
        "lease_token": None,
        "lease_until": None,
        "error_code": error.code if error else None,
        "error_message": error.message if error else None,
    }


def timeout_failure() -> ReviewJobFailure:
    return ReviewJobFailure(
        "REVIEW_FAILED", "The review exceeded its time limit; try a smaller document"
    )


class ReviewJobs:
    """Process-local dispatcher with database-enforced capacity and fenced commits."""

    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        process: Callable[
            [ReviewJobModel], Awaitable[tuple[Specification, ReviewReport]]
        ],
        checkpoint_root: Path,
        *,
        provider_identity: str,
    ):
        self.sessions = sessions
        self.process = process
        self.checkpoint_root = checkpoint_root.resolve()
        self.provider_identity = provider_identity
        self._tasks: dict[str, tuple[str, asyncio.Task]] = {}
        self._signalled: set[str] = set()
        self._wake = asyncio.Event()
        self._dispatcher: asyncio.Task | None = None
        self._stopping = False
        self._last_maintenance = 0.0

    def start(self) -> None:
        if self._dispatcher is None:
            self._dispatcher = asyncio.create_task(self._dispatch())
        self._wake.set()

    def checkpoint_path(self, job_id: str) -> Path:
        return self.checkpoint_root / str(UUID(job_id))

    def _delete_checkpoint(self, job_id: str) -> None:
        path = self.checkpoint_path(job_id)
        try:
            if path.is_symlink():
                path.unlink()
            elif path.is_dir():
                shutil.rmtree(path)
        except OSError as error:
            logger.warning(
                "review_checkpoint_cleanup_failed", error_type=type(error).__name__
            )

    async def _cleanup_terminal(
        self, job_id: str, started_at: datetime, *, prune: bool = False
    ) -> None:
        # Lock exactly this terminal generation while removing its checkpoint.
        # A delayed cleanup from another dispatcher cannot delete the files
        # of a subsequently accepted job that reuses an expired UUID.
        path = self.checkpoint_path(job_id)
        if not prune and not path.exists() and not path.is_symlink():
            return
        fence = (
            ReviewJobModel.id == job_id,
            ReviewJobModel.started_at == started_at,
            ReviewJobModel.status.in_(TERMINAL_STATUSES),
            ReviewJobModel.active_slot.is_(None),
        )
        async with self.sessions.begin() as session:
            locked = await session.execute(
                update(ReviewJobModel)
                .where(*fence)
                .values(finished_at=ReviewJobModel.finished_at)
                .execution_options(synchronize_session=False)
            )
            if locked.rowcount != 1:
                return
            cleanup = asyncio.create_task(
                asyncio.to_thread(self._delete_checkpoint, job_id)
            )
            try:
                await asyncio.shield(cleanup)
            except asyncio.CancelledError:
                await asyncio.gather(cleanup, return_exceptions=True)
                raise
            if prune and not path.exists() and not path.is_symlink():
                await session.execute(
                    delete(ReviewJobModel)
                    .where(*fence)
                    .execution_options(synchronize_session=False)
                )

    async def admit(
        self,
        owner_id: str,
        job_id: str,
        filename: str,
        payload_metadata: dict,
        primary_bytes: bytes,
        reference_bytes: bytes,
    ) -> ReviewJobModel:
        # UNIQUE nullable slots and owner columns enforce the limits even when
        # several processes race between the read and insert. Retry only those
        # insertion races; never replace accepted data for an existing UUID.
        await self._release_expired()
        for _ in range(MAX_RUNNING_REVIEW_JOBS + 2):
            async with self.sessions() as session:
                existing = await session.scalar(
                    metadata_query().where(ReviewJobModel.id == job_id)
                )
                if existing is not None:
                    if existing.owner_id != owner_id:
                        raise PermissionError("Review job not found")
                    return existing
                archived = (
                    await session.execute(
                        select(
                            CompletedReview.owner_id,
                            CompletedReview.filename,
                            CompletedReview.created_at,
                        ).where(CompletedReview.id == job_id)
                    )
                ).one_or_none()
                if archived is not None:
                    if archived.owner_id != owner_id:
                        raise PermissionError("Review job not found")
                    return ReviewJobModel(
                        id=job_id,
                        owner_id=owner_id,
                        filename=archived.filename,
                        started_at=archived.created_at,
                        status="completed",
                    )
                busy = (
                    await session.execute(
                        select(
                            ReviewJobModel.active_slot, ReviewJobModel.active_owner
                        ).where(ReviewJobModel.active_slot.is_not(None))
                    )
                ).all()
                occupied = {slot for slot, _ in busy}
                slot = next(
                    (
                        number
                        for number in range(1, MAX_RUNNING_REVIEW_JOBS + 1)
                        if number not in occupied
                    ),
                    None,
                )
                if slot is None or any(owner == owner_id for _, owner in busy):
                    raise ReviewJobCapacityError(
                        "A review is already running or all review slots are busy"
                    )
                now = datetime.now(UTC)
                job = ReviewJobModel(
                    id=job_id,
                    owner_id=owner_id,
                    filename=filename,
                    status="pending",
                    started_at=now,
                    deadline_at=now + timedelta(seconds=MAX_REVIEW_JOB_SECONDS),
                    remaining_seconds=MAX_REVIEW_JOB_SECONDS,
                    active_owner=owner_id,
                    active_slot=slot,
                    payload_metadata=payload_metadata,
                    primary_bytes=primary_bytes,
                    reference_bytes=reference_bytes,
                )
                session.add(job)
                try:
                    await session.commit()
                except IntegrityError:
                    await session.rollback()
                    continue
                self._wake.set()
                return job
        raise ReviewJobCapacityError("All review slots are busy")

    @staticmethod
    def _stale(now: datetime):
        return or_(
            ReviewJobModel.lease_token.is_(None), ReviewJobModel.lease_until <= now
        )

    async def _release_expired(self) -> None:
        now = datetime.now(UTC)
        async with self.sessions.begin() as session:
            # A cancelled native worker retains its capacity slot until it
            # drains, or until its dead process's lease actually expires.
            await session.execute(
                update(ReviewJobModel)
                .where(
                    ReviewJobModel.status == "cancelled",
                    ReviewJobModel.active_slot.is_not(None),
                    self._stale(now),
                )
                .values(**terminal_values("cancelled"))
            )
            await session.execute(
                update(ReviewJobModel)
                .where(
                    ReviewJobModel.status == "pending",
                    ReviewJobModel.remaining_seconds <= 0,
                    self._stale(now),
                )
                .values(**terminal_values("failed", error=timeout_failure()))
            )

    async def cancel(self, owner_id: str, job_id: str) -> ReviewJobModel | None:
        now = datetime.now(UTC)
        async with self.sessions.begin() as session:
            await session.execute(
                update(ReviewJobModel)
                .where(
                    ReviewJobModel.id == job_id,
                    ReviewJobModel.owner_id == owner_id,
                    ReviewJobModel.status.in_(ACTIVE_STATUSES),
                )
                .values(
                    status="cancelled",
                    finished_at=now,
                    payload_metadata=None,
                    primary_bytes=None,
                    reference_bytes=None,
                )
            )
            await session.execute(
                update(ReviewJobModel)
                .where(
                    ReviewJobModel.id == job_id,
                    ReviewJobModel.owner_id == owner_id,
                    ReviewJobModel.status == "cancelled",
                    ReviewJobModel.active_slot.is_not(None),
                    self._stale(now),
                )
                .values(**terminal_values("cancelled"))
            )
            job = await get_review_job(session, owner_id, job_id)
        if job is not None and job.status == "cancelled":
            self._signal(job_id)
            if job.active_slot is None:
                await self._cleanup_terminal(job_id, job.started_at)
        self._wake.set()
        return job

    def _signal(self, job_id: str) -> None:
        claimed = self._tasks.get(job_id)
        if claimed and job_id not in self._signalled:
            self._signalled.add(job_id)
            claimed[1].cancel()

    async def _claim(self, job_id: str) -> ReviewJobModel | None:
        now = datetime.now(UTC)
        token = str(uuid4())
        async with self.sessions.begin() as session:
            candidate = await session.scalar(
                metadata_query().where(
                    ReviewJobModel.id == job_id,
                    ReviewJobModel.status.in_(ACTIVE_STATUSES),
                    ReviewJobModel.active_slot.is_not(None),
                    self._stale(now),
                )
            )
            if candidate is None:
                return None
            remaining = candidate.remaining_seconds
            if candidate.lease_token and candidate.lease_until:
                # An abrupt process crash has no final active-time accounting.
                # Charge through its last heartbeat plus one heartbeat period,
                # not the entire container downtime or stale-lease wait.
                confirmed_through = utc(candidate.lease_until) - timedelta(
                    seconds=LEASE_SECONDS - LEASE_REFRESH_SECONDS
                )
                remaining = min(
                    remaining,
                    max(
                        0,
                        (
                            utc(candidate.deadline_at) - confirmed_through
                        ).total_seconds(),
                    ),
                )
            fence = (
                update(ReviewJobModel)
                .where(
                    ReviewJobModel.id == job_id,
                    ReviewJobModel.status == candidate.status,
                    ReviewJobModel.lease_token == candidate.lease_token,
                    ReviewJobModel.remaining_seconds == candidate.remaining_seconds,
                    ReviewJobModel.active_slot.is_not(None),
                    self._stale(now),
                )
                .execution_options(synchronize_session=False)
            )
            if remaining <= 0:
                await session.execute(
                    fence.values(**terminal_values("failed", error=timeout_failure()))
                )
                return None
            changed = await session.execute(
                fence.values(
                    status="running",
                    lease_token=token,
                    lease_until=now + timedelta(seconds=LEASE_SECONDS),
                    deadline_at=now + timedelta(seconds=remaining),
                    remaining_seconds=remaining,
                )
            )
            if changed.rowcount != 1:
                return None
            # Refresh the metadata-only candidate from the winning row. ORM
            # synchronization is intentionally disabled: SQLite returns naive
            # timestamps, while our database fence compares UTC instants.
            return await session.scalar(
                select(ReviewJobModel)
                .where(ReviewJobModel.id == job_id, ReviewJobModel.lease_token == token)
                .execution_options(populate_existing=True)
            )

    async def _renew(self, job_id: str, token: str) -> str | None:
        now = datetime.now(UTC)
        async with self.sessions.begin() as session:
            return (
                await session.execute(
                    update(ReviewJobModel)
                    .where(
                        ReviewJobModel.id == job_id,
                        ReviewJobModel.lease_token == token,
                        ReviewJobModel.lease_until > now,
                    )
                    .values(lease_until=now + timedelta(seconds=LEASE_SECONDS))
                    .returning(ReviewJobModel.status)
                )
            ).scalar_one_or_none()

    async def _heartbeat(self, job_id: str, token: str) -> None:
        last_success = monotonic()
        while True:
            await asyncio.sleep(LEASE_REFRESH_SECONDS)
            try:
                status = await self._renew(job_id, token)
                if status is None:
                    self._signal(job_id)
                    return
                last_success = monotonic()
                if status == "cancelled":
                    self._signal(job_id)
            except asyncio.CancelledError:
                raise
            except Exception as error:
                logger.warning(
                    "review_job_heartbeat_failed", error_type=type(error).__name__
                )
                if monotonic() - last_success >= LEASE_SECONDS:
                    self._signal(job_id)
                    return

    async def _release_finished_worker(self, job_id: str, token: str) -> None:
        # The token fence means an expired worker cannot release a successor's
        # lease. Unexpected task cancellation requeues; explicit cancellation
        # remains terminal. It also covers a task cancelled before first entry.
        async with self.sessions.begin() as session:
            locked = await session.execute(
                update(ReviewJobModel)
                .where(ReviewJobModel.id == job_id, ReviewJobModel.lease_token == token)
                .values(lease_token=ReviewJobModel.lease_token)
                .execution_options(synchronize_session=False)
            )
            if locked.rowcount != 1:
                return
            existing = await session.scalar(
                metadata_query().where(
                    ReviewJobModel.id == job_id, ReviewJobModel.lease_token == token
                )
            )
            if existing is None:
                return
            released = False
            if existing.status == "cancelled":
                result = await session.execute(
                    update(ReviewJobModel)
                    .where(
                        ReviewJobModel.id == job_id,
                        ReviewJobModel.lease_token == token,
                        ReviewJobModel.status == "cancelled",
                    )
                    .values(**terminal_values("cancelled"))
                    .execution_options(synchronize_session=False)
                )
                released = result.rowcount == 1
            elif existing.status == "running":
                # Graceful stops account only for actual worker runtime. A
                # pending row's remaining allowance then stays unchanged on disk.
                until = (
                    min(datetime.now(UTC), utc(existing.lease_until))
                    if existing.lease_until
                    else datetime.now(UTC)
                )
                remaining = max(0, (utc(existing.deadline_at) - until).total_seconds())
                fence = (
                    update(ReviewJobModel)
                    .where(
                        ReviewJobModel.id == job_id,
                        ReviewJobModel.lease_token == token,
                        ReviewJobModel.status == "running",
                    )
                    .execution_options(synchronize_session=False)
                )
                if remaining <= 0:
                    result = await session.execute(
                        fence.values(
                            **terminal_values("failed", error=timeout_failure())
                        )
                    )
                    released = result.rowcount == 1
                else:
                    await session.execute(
                        fence.values(
                            status="pending",
                            remaining_seconds=remaining,
                            lease_token=None,
                            lease_until=None,
                        )
                    )
        if released:
            await self._cleanup_terminal(job_id, existing.started_at)

    async def _finish(
        self,
        job: ReviewJobModel,
        *,
        specification: Specification | None = None,
        report: ReviewReport | None = None,
        error: ReviewJobFailure | None = None,
    ) -> bool:
        # The terminal state and owner-scoped archive are a single transaction.
        # Acquire the row by a fenced UPDATE *before* inserting the archive;
        # a cancelled or replaced lease must never commit a late result.
        status = "failed" if error else "completed"
        now = datetime.now(UTC)
        async with self.sessions.begin() as session:
            changed = await session.execute(
                update(ReviewJobModel)
                .where(
                    ReviewJobModel.id == job.id,
                    ReviewJobModel.owner_id == job.owner_id,
                    ReviewJobModel.lease_token == job.lease_token,
                    ReviewJobModel.lease_until > now,
                    ReviewJobModel.status == "running",
                )
                .values(**terminal_values(status, error=error))
            )
            if changed.rowcount != 1:
                return False
            if not error:
                archive = ReviewArchive(session)
                if await archive.get(job.owner_id, job.id) is None:
                    if (
                        specification is None
                        or report is None
                        or report.passport.run_id != job.id
                    ):
                        raise RuntimeError("Review result has no matching run ID")
                    await archive.save(
                        job.owner_id, specification, report, commit=False
                    )
        await self._cleanup_terminal(job.id, job.started_at)
        return True

    async def _run(self, job: ReviewJobModel) -> None:
        mode = (job.payload_metadata or {}).get("review_mode", "legacy")
        with review_run(job.id, review_mode=mode, source="worker") as run:
            await self._run_attempt(job, run)

    async def _run_attempt(self, job: ReviewJobModel, run: ReviewRun) -> None:
        heartbeat = asyncio.create_task(self._heartbeat(job.id, job.lease_token))
        try:
            # Recheck the durable intent after claiming; a remote cancellation
            # may have committed while this task was waiting to be scheduled.
            if await self._renew(job.id, job.lease_token) != "running":
                run.outcome("not_committed")
                return
            specification = report = None
            failure = None
            try:
                directory = self.checkpoint_path(job.id)
                if directory.is_symlink():
                    raise RuntimeError("Invalid review checkpoint directory")
                checkpoint = StageCheckpoint(
                    directory, provider_identity=self.provider_identity
                )
                remaining = (utc(job.deadline_at) - datetime.now(UTC)).total_seconds()
                async with asyncio.timeout(max(0, remaining)):
                    with checkpoint_scope(checkpoint):
                        specification, report = await self.process(job)
            except TimeoutError:
                failure = timeout_failure()
            except ReviewJobFailure as error:
                failure = error
            except Exception as error:
                logger.warning("review_job_failed", error_type=type(error).__name__)
                failure = ReviewJobFailure(
                    "REVIEW_FAILED", "The agent could not produce a valid review"
                )
            # Shield the atomic commit: cancellation before the row lock wins
            # is safe; after commit the completed archive remains authoritative.
            # Commit errors are not model failures: the dispatcher requeues the
            # fenced job and retains its accepted stage checkpoints.
            if report is not None:
                run.report(report)
            async def persist():
                with review_stage("archive") as stage:
                    committed = await self._finish(
                        job, specification=specification, report=report, error=failure
                    )
                    stage.outcome("completed" if committed else "not_committed")
                    return committed

            finish = asyncio.create_task(persist())
            try:
                committed = await asyncio.shield(finish)
                if not committed:
                    run.outcome("not_committed")
                elif failure is None:
                    run.outcome("completed")
                else:
                    # Mark terminal failure only after the fenced durable commit.
                    run.failure(failure.code)
            except asyncio.CancelledError:
                await asyncio.gather(finish, return_exceptions=True)
                raise
        finally:
            heartbeat.cancel()
            await asyncio.gather(heartbeat, return_exceptions=True)

    async def _reap_finished_tasks(self) -> None:
        for job_id, (token, task) in list(self._tasks.items()):
            if not task.done():
                continue
            if not task.cancelled() and (error := task.exception()) is not None:
                logger.warning(
                    "review_job_worker_ended", error_type=type(error).__name__
                )
            await self._release_finished_worker(job_id, token)
            self._tasks.pop(job_id, None)
            self._signalled.discard(job_id)

    async def _maintenance(self) -> None:
        # Crash between terminal DB commit and directory removal is harmless:
        # startup/periodic maintenance repeats the removal before old metadata
        # is pruned. Never remove a directory belonging to an active slot.
        now = datetime.now(UTC)
        async with self.sessions() as session:
            terminal = (
                await session.execute(
                    select(
                        ReviewJobModel.id,
                        ReviewJobModel.started_at,
                        ReviewJobModel.finished_at,
                    )
                    .where(
                        ReviewJobModel.status.in_(TERMINAL_STATUSES),
                        ReviewJobModel.active_slot.is_(None),
                    )
                    .order_by(ReviewJobModel.finished_at.desc())
                )
            ).all()
        for index, (job_id, started_at, finished_at) in enumerate(terminal):
            expired = finished_at is not None and (
                index >= MAX_FINISHED_REVIEW_JOBS
                or (now - utc(finished_at)).total_seconds()
                >= FINISHED_REVIEW_JOB_SECONDS
            )
            await self._cleanup_terminal(job_id, started_at, prune=expired)

    async def maintain_without_provider(self) -> None:
        # Pending jobs wait for model configuration. Clean previous terminal
        # data at startup but do not poll a shared in-memory DB in background.
        await self._release_expired()
        await self._maintenance()

    async def _dispatch(self) -> None:
        while not self._stopping:
            self._wake.clear()
            try:
                await self._reap_finished_tasks()
                await self._release_expired()
                if monotonic() - self._last_maintenance >= LEASE_REFRESH_SECONDS:
                    await self._maintenance()
                    self._last_maintenance = monotonic()
                now = datetime.now(UTC)
                async with self.sessions() as session:
                    available = (
                        await session.scalars(
                            select(ReviewJobModel.id)
                            .where(
                                ReviewJobModel.status.in_(ACTIVE_STATUSES),
                                ReviewJobModel.active_slot.is_not(None),
                                self._stale(now),
                            )
                            .order_by(ReviewJobModel.started_at)
                            .limit(MAX_RUNNING_REVIEW_JOBS)
                        )
                    ).all()
                for job_id in available:
                    if self._stopping:
                        break
                    if job_id in self._tasks:
                        continue
                    claimed = await self._claim(job_id)
                    if claimed is None:
                        continue
                    task = asyncio.create_task(self._run(claimed))
                    self._tasks[job_id] = (claimed.lease_token, task)
                    task.add_done_callback(lambda _: self._wake.set())
            except asyncio.CancelledError:
                raise
            except Exception as error:
                logger.warning(
                    "review_job_dispatch_failed", error_type=type(error).__name__
                )
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=DISPATCH_SECONDS)
            except TimeoutError:
                pass

    async def stop(self) -> None:
        self._stopping = True
        if self._dispatcher is not None:
            # Let an in-flight claim transaction return before stopping. A
            # task interrupted before its claim was registered would otherwise
            # retain an unnecessary stale lease until the next startup.
            self._wake.set()
            await asyncio.gather(self._dispatcher, return_exceptions=True)
        for job_id in self._tasks:
            self._signal(job_id)
        await asyncio.gather(
            *(task for _, task in self._tasks.values()), return_exceptions=True
        )
        await self._reap_finished_tasks()
