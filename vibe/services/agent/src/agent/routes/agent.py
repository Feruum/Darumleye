"""Module catalog and streaming chat API for the Bun server proxy."""

import asyncio
import json
import re
import time
from contextlib import aclosing, suppress
from uuid import UUID, uuid4

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from langchain_core.messages import AIMessage, AIMessageChunk
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from agent.config.settings import settings
from agent.core.agent import Agent
from agent.infrastructure.database import get_session
from agent.infrastructure.stream_admission import AdmissionRejected, StreamLease
from agent.infrastructure.thread_ownership import (
    check_thread_access,
    ensure_thread_ownership,
    get_thread_module,
)
from agent.modules.registry import UnknownModuleError, module_catalog, select_module
from agent.services.review_archive import ReviewArchive
from agent.server_auth import UserProfile, get_current_user
from agent.utils.observability import (
    record_first_token,
    record_output_chunk,
    record_stream_admitted,
    record_stream_finished,
    record_stream_rejected,
)
from agent.utils.rate_limit import limiter

router = APIRouter(prefix="/api/v1", tags=["agent"])
logger = structlog.get_logger(__name__)


class ChatMessage(BaseModel):
    type: str = Field(..., pattern="^(human|assistant)$")
    content: str = Field(..., min_length=1, max_length=32000)


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    messages: list[ChatMessage] = Field(..., min_length=1, max_length=50)
    thread_id: str | None = Field(None, max_length=128, pattern="^[A-Za-z0-9._:-]{1,128}$")
    module_id: str | None = Field(None, max_length=64)
    review_id: UUID | None = None


def _sse(event: str, payload: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False, default=str)}\n\n"


def _token_text(chunk: AIMessage | AIMessageChunk) -> str:
    if isinstance(chunk.content, str):
        return chunk.content
    if isinstance(chunk.content, list):
        return "".join(
            str(block.get("text", ""))
            for block in chunk.content
            if isinstance(block, dict) and block.get("type") == "text"
        )
    return ""


async def _with_keepalive(chunks, interval: float = 8.0):
    """Keep a pending model read alive without cancelling it at each idle interval."""
    iterator = chunks.__aiter__()
    pending = None
    try:
        while True:
            pending = asyncio.create_task(anext(iterator))
            while not pending.done():
                done, _ = await asyncio.wait({pending}, timeout=interval)
                if not done:
                    yield None
            try:
                chunk = pending.result()
            except StopAsyncIteration:
                return
            finally:
                pending = None
            yield chunk
    finally:
        if pending is not None:
            pending.cancel()
            with suppress(asyncio.CancelledError, StopAsyncIteration):
                await pending
        await iterator.aclose()


@router.get("/modules")
@limiter.limit("60/minute")
async def list_modules(request: Request):
    return {"modules": module_catalog()}


@router.get("/models")
@limiter.limit("60/minute")
async def list_models(request: Request):
    return {"models": settings.get_available_models(), "default_model": settings.llm_model}


@router.post("/chat/stream")
@limiter.limit(settings.rate_limit_per_user)
async def stream_chat(
    request: Request,
    body: ChatRequest,
    user: UserProfile = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    last_user_message = next(
        (message.content for message in reversed(body.messages) if message.type == "human"),
        None,
    )
    if not last_user_message:
        raise HTTPException(status_code=422, detail="A human message is required")
    try:
        module = select_module(last_user_message, body.module_id)
    except UnknownModuleError as error:
        raise HTTPException(status_code=422, detail={
            "code": "UNKNOWN_MODULE",
            "message": f"Module '{error}' is not enabled",
        }) from error

    requested_thread_id = body.thread_id or request.headers.get("X-Thread-ID")
    if requested_thread_id and not re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", requested_thread_id):
        raise HTTPException(status_code=422, detail="Invalid thread_id")
    review_id = str(body.review_id) if body.review_id else None
    if module.id == "review_chat":
        if not review_id:
            raise HTTPException(status_code=422, detail="Select a completed review")
        if await ReviewArchive(session).get(user.user_id, review_id) is None:
            raise HTTPException(status_code=404, detail="Completed review not found")
        prefix = f"review:{review_id}:"
        if requested_thread_id and not requested_thread_id.startswith(prefix):
            raise HTTPException(status_code=422, detail="Thread does not belong to this review")
        thread_id = requested_thread_id or f"{prefix}{uuid4()}"
    else:
        if review_id:
            raise HTTPException(status_code=422, detail="Review ID requires review chat")
        thread_id = requested_thread_id or str(uuid4())
    if not await ensure_thread_ownership(thread_id, user.user_id, module.id):
        raise HTTPException(status_code=403, detail="Thread access denied")

    agent: Agent | None = request.app.state.agents.get(module.id)
    if agent is None:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "LLM_NOT_CONFIGURED",
                "message": "Configure the chat provider to enable agent responses",
            },
        )

    try:
        lease = await request.app.state.stream_admission.acquire(user.user_id)
    except AdmissionRejected as error:
        record_stream_rejected(error.reason)
        raise HTTPException(
            status_code=429,
            detail={"code": "STREAM_CAPACITY_EXCEEDED", "message": "Retry shortly"},
            headers={"Retry-After": str(error.retry_after_seconds)},
        ) from error

    record_stream_admitted()
    return await _stream_response(agent, last_user_message, thread_id, user.user_id, lease, review_id)


async def _stream_response(
    agent: Agent,
    message: str,
    thread_id: str,
    user_id: str,
    lease: StreamLease,
    review_id: str | None = None,
) -> StreamingResponse:
    async def event_generator():
        stream_started = time.perf_counter()
        first_token_recorded = False
        stream_failed = False
        lease_lost = asyncio.Event()
        heartbeat = asyncio.create_task(lease.heartbeat(lease_lost))
        try:
            yield _sse("module", {"module_id": agent.module.id, "thread_id": thread_id})
            chunks = _with_keepalive(agent.astream(message, thread_id, user_id, **({"review_id": review_id} if review_id else {})))
            async with aclosing(chunks):
                async for chunk in chunks:
                    if lease_lost.is_set():
                        raise RuntimeError("stream admission lease lost")
                    if chunk is None:
                        yield ": keepalive\n\n"
                        continue
                    if not isinstance(chunk, tuple) or len(chunk) != 2:
                        continue
                    mode, data = chunk
                    if mode == "messages":
                        token = _token_text(data[0]) if isinstance(data, tuple) and isinstance(data[0], AIMessageChunk) else ""
                        if token:
                            record_output_chunk(agent.model_name)
                            if not first_token_recorded:
                                record_first_token(time.perf_counter() - stream_started, agent.model_name)
                                first_token_recorded = True
                            yield _sse("token", {"content": token})
                        continue
                    if mode == "updates" and agent.module.id == "review_chat" and isinstance(data, dict):
                        messages = data.get("agent", {}).get("messages", [])
                        token = _token_text(messages[-1]) if messages and isinstance(messages[-1], AIMessage) else ""
                        if token:
                            record_output_chunk(agent.model_name)
                            if not first_token_recorded:
                                record_first_token(time.perf_counter() - stream_started, agent.model_name)
                                first_token_recorded = True
                            yield _sse("token", {"content": token})
                        continue
                    if mode != "custom" or not isinstance(data, dict):
                        continue
                    event_type = data.get("type")
                    if event_type == "tool_call_start":
                        yield _sse("tool_status", {
                            "tool": data.get("tool"),
                            "id": data.get("id"),
                            "status": "running",
                        })
                    elif event_type == "tool_call_end":
                        yield _sse("tool_status", {
                            "tool": data.get("tool"),
                            "id": data.get("id"),
                            "status": data.get("status", "ok"),
                            **({"error": data["error"]} if data.get("error") else {}),
                        })
                    elif event_type == "sources":
                        for source in data.get("sources", []):
                            if isinstance(source, dict):
                                yield _sse("source", source)
            if agent.module.id == "review_chat" and not first_token_recorded:
                raise RuntimeError("Review chat returned no answer")
            yield _sse("end", {})
        except asyncio.CancelledError:
            logger.info("agent_stream_cancelled", module_id=agent.module.id, duration_seconds=round(time.perf_counter() - stream_started, 1))
            raise
        except Exception:
            stream_failed = True
            logger.exception("agent_stream_failed", module_id=agent.module.id, user_id=user_id)
            yield _sse("error", {"error": "The agent could not finish this request."})
            yield _sse("end", {})
        finally:
            heartbeat.cancel()
            with suppress(asyncio.CancelledError):
                await heartbeat
            try:
                await lease.release()
            except Exception:
                logger.exception("stream_admission_release_failed", user_id=user_id)
            record_stream_finished(
                time.perf_counter() - stream_started,
                model=agent.model_name,
                error=stream_failed,
            )

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
            "X-Thread-ID": thread_id,
        },
    )


@router.get("/threads/{thread_id}")
async def get_thread_history(
    request: Request,
    thread_id: str,
    user: UserProfile = Depends(get_current_user),
):
    if not await check_thread_access(thread_id, user.user_id):
        raise HTTPException(status_code=404, detail="Thread not found")
    module_id = await get_thread_module(thread_id, user.user_id)
    agent = request.app.state.agents.get(module_id or "")
    if agent is None:
        raise HTTPException(status_code=404, detail="Thread module is no longer enabled")
    return {"thread_id": thread_id, "module_id": module_id, "messages": await agent.get_thread_history(thread_id, user.user_id)}


@router.delete("/threads/{thread_id}")
async def delete_thread(
    request: Request,
    thread_id: str,
    user: UserProfile = Depends(get_current_user),
):
    if not await check_thread_access(thread_id, user.user_id):
        raise HTTPException(status_code=404, detail="Thread not found")
    module_id = await get_thread_module(thread_id, user.user_id)
    agent = request.app.state.agents.get(module_id or "")
    if agent is None or not await agent.delete_thread(thread_id, user.user_id):
        raise HTTPException(status_code=500, detail="Failed to delete thread")
    return {"status": "deleted", "thread_id": thread_id}
