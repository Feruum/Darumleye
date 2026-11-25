from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from agent.routes.agent import _sse, router
from agent.server_auth import UserProfile, get_current_user
from agent.utils.rate_limit import limiter


def make_test_app() -> FastAPI:
    app = FastAPI()
    app.include_router(router, dependencies=[Depends(get_current_user)])
    app.dependency_overrides[get_current_user] = lambda: UserProfile(user_id="test-user")
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
    return app


def test_modules_endpoint_lists_enabled_modules():
    response = TestClient(make_test_app()).get("/api/v1/modules")

    assert response.status_code == 200
    modules = response.json()["modules"]
    assert modules[0]["id"] == "rag"
    assert modules[0]["name"] == "Document Q&A"


def test_invalid_module_is_rejected_before_starting_a_chat():
    response = TestClient(make_test_app()).post(
        "/api/v1/chat/stream",
        json={
            "messages": [{"type": "human", "content": "hello"}],
            "module_id": "unknown-module",
        },
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "UNKNOWN_MODULE"


def test_sse_uses_named_events_and_json_data():
    assert _sse("token", {"content": "ответ"}) == (
        'event: token\ndata: {"content": "ответ"}\n\n'
    )


def test_silent_model_yields_keepalives_without_cancelling_pending_chunk():
    import asyncio
    from agent.routes.agent import _with_keepalive

    async def run():
        async def chunks():
            await asyncio.sleep(.03)
            yield ("custom", {"type": "sources"})
        return [chunk async for chunk in _with_keepalive(chunks(), interval=.005)]

    events = asyncio.run(run())
    assert events[0] is None
    assert events[-1] == ("custom", {"type": "sources"})
    assert events.count(("custom", {"type": "sources"})) == 1


def test_review_chat_stream_sends_final_model_message_and_source():
    import asyncio
    from types import SimpleNamespace
    from langchain_core.messages import AIMessage
    from agent.routes.agent import _stream_response

    async def run():
        class Lease:
            released = False

            async def heartbeat(self, lost):
                await asyncio.Future()

            async def release(self):
                self.released = True

        async def astream(*args, **kwargs):
            yield ("custom", {"type": "sources", "sources": [{"filename": "spec.txt"}]})
            yield ("updates", {"agent": {"messages": [AIMessage(content="Ответ на основе ТЗ.")]}})

        lease = Lease()
        agent = SimpleNamespace(module=SimpleNamespace(id="review_chat"), model_name="fixture", astream=astream)
        response = await _stream_response(agent, "Question", "thread", "owner", lease, "review")
        body = "".join([frame async for frame in response.body_iterator])
        assert lease.released
        return body

    body = asyncio.run(run())
    assert 'event: source' in body and 'spec.txt' in body
    assert 'event: token' in body and 'Ответ на основе ТЗ.' in body
    assert 'event: end' in body and 'event: error' not in body
