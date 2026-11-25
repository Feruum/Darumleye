from unittest.mock import AsyncMock, MagicMock

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.tools import tool

from agent.graphs.workflow import _strip_reasoning_from_message, create_workflow
from agent.modules.base import AgentModule
from agent.modules.registry import get_module


def test_strip_reasoning_from_ai_message():
    original = AIMessage(
        content=[{"type": "reasoning", "reasoning": "hidden"}, {"type": "text", "text": "ok"}],
        additional_kwargs={"reasoning_content": "hidden", "keep": "yes"},
    )

    cleaned = _strip_reasoning_from_message(original)

    assert cleaned.content == [{"type": "text", "text": "ok"}]
    assert cleaned.additional_kwargs == {"keep": "yes"}


def test_workflow_binds_only_selected_module_tools():
    llm = MagicMock()
    llm.bind_tools.return_value = llm

    create_workflow(llm, get_module("rag"))

    args, kwargs = llm.bind_tools.call_args
    tools = args[0]
    assert [item.name for item in tools] == ["search_user_documents", "search_user_memory"]
    assert kwargs == {"tool_choice": "auto"}


@pytest.mark.asyncio
async def test_workflow_basic_conversation_uses_module_prompt():
    llm = MagicMock()
    llm.bind_tools.return_value = llm
    llm.ainvoke = AsyncMock(return_value=AIMessage(content="Hello"))
    module = AgentModule(
        id="hello",
        name="Hello",
        description="A small test module",
        instructions="Reply with a greeting.",
    )

    result = await create_workflow(llm, module).compile().ainvoke(
        {"messages": [HumanMessage(content="Hi")], "user_id": "user-1"},
        {"configurable": {"thread_id": "basic"}},
    )

    assert result["messages"][-1].content == "Hello"
    assert llm.ainvoke.await_args.args[0][0].content == "Reply with a greeting."


@pytest.mark.asyncio
async def test_model_cannot_override_trusted_tool_user_id():
    captured_user_ids: list[str] = []

    @tool
    def record_user(user_id: str = "") -> str:
        """Record the effective user id for this test."""
        captured_user_ids.append(user_id)
        return "ok"

    module = AgentModule(
        id="test",
        name="Test",
        description="Trust boundary test",
        instructions="Use the tool.",
        tools=(record_user,),
        trusted_tool_arguments={"record_user": "user_id"},
    )
    llm = MagicMock()
    llm.bind_tools.return_value = llm
    llm.ainvoke = AsyncMock(
        side_effect=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "record_user",
                        "args": {"user_id": "forged-user"},
                        "id": "call-1",
                        "type": "tool_call",
                    }
                ],
            ),
            AIMessage(content="done"),
        ]
    )

    result = await create_workflow(llm, module).compile().ainvoke(
        {"messages": [HumanMessage(content="Run it")], "user_id": "trusted-user"},
        {"configurable": {"thread_id": "trusted"}},
    )

    assert result["messages"][-1].content == "done"
    assert captured_user_ids == ["trusted-user"]


@pytest.mark.asyncio
async def test_review_chat_tool_uses_only_trusted_selected_review_and_owner():
    captured = []

    @tool
    def record_review(user_id: str = "", review_id: str = "") -> str:
        """Record the selected review and owner for this test."""
        captured.append((user_id, review_id))
        return "ok"

    module = AgentModule(
        id="test", name="Test", description="Trust boundary", instructions="Use the tool.",
        tools=(record_review,), trusted_tool_arguments={"record_review": ("user_id", "review_id")},
    )
    llm = MagicMock()
    llm.bind_tools.return_value = llm
    llm.ainvoke = AsyncMock(side_effect=[
        AIMessage(content="", tool_calls=[{"name": "record_review", "args": {
            "user_id": "wrong", "review_id": "wrong"}, "id": "call-1"}]),
        AIMessage(content="done"),
    ])
    await create_workflow(llm, module).compile().ainvoke({
        "messages": [HumanMessage(content="Question")], "user_id": "owner", "review_id": "selected",
    })
    assert captured == [("owner", "selected")]


@pytest.mark.asyncio
async def test_review_chat_prefetches_evidence_without_a_model_tool_round_trip():
    calls = []

    @tool
    async def search_user_review(query: str, user_id: str = "", review_id: str = "") -> str:
        """Retrieve the selected review."""
        calls.append((query, user_id, review_id))
        return '{"context":"Original line 9: 100000", "sources":[{"id":"S1","filename":"requirements.txt"},{"id":"S2","filename":"unused.txt"}]}'

    module = AgentModule(id="review_chat", name="Review", description="Review", instructions="Use evidence.", tools=(search_user_review,))
    llm = MagicMock()
    llm.bind_tools.return_value = llm
    llm.ainvoke = AsyncMock(return_value=AIMessage(content='{"answer":"100000","citations":["S1","unknown"]}'))
    events = []
    graph = create_workflow(llm, module).compile()
    async for event in graph.astream({"messages": [HumanMessage(content="What is the limit?")],
                                     "user_id": "owner", "review_id": "selected"},
                                    stream_mode=["custom", "updates"]):
        events.append(event)
    assert calls == [("What is the limit?", "owner", "selected")]
    assert llm.ainvoke.await_count == 1
    assert "Original line 9: 100000" in llm.ainvoke.await_args.args[0][-1].content
    assert not llm.bind_tools.called
    assert [data["sources"] for mode, data in events if mode == "custom" and data.get("type") == "sources"] == [[{"id": "S1", "filename": "requirements.txt"}]]

    llm.ainvoke = AsyncMock(return_value=AIMessage(content='{"answer":"Уточните вопрос.","citations":[]}'))
    clarification_events = []
    async for event in create_workflow(llm, module).compile().astream({"messages": [HumanMessage(content="ыфвфы")],
            "user_id": "owner", "review_id": "selected"}, stream_mode=["custom", "updates"]):
        clarification_events.append(event)
    assert not any(mode == "custom" and data.get("type") == "sources" for mode, data in clarification_events)
