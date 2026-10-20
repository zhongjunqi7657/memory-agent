"""Real provider-style chunks, including split tool calls and cancellation."""

import asyncio
from uuid import UUID, uuid4

import pytest
from langchain_core.language_models.fake_chat_models import FakeChatModel
from langchain_core.messages import (
    AIMessageChunk,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langchain_core.outputs import ChatGenerationChunk
from sqlalchemy import delete, select

from app.agent.graph import build_graph
from app.agent.service import AgentService
from app.agent.streaming import stream_model
from app.config.business import BusinessConfig
from app.config.settings import Settings
from app.execution.controller import RunController
from app.persistence.models import Message, MessageRole, Run, RunEvent, RunStatus, User
from app.protocol.tools import ToolExecutionContext, ToolResult
from app.tools.builtin.memory import MemoryTool
from app.tools.executor import ToolExecutor
from app.tools.registry import ToolRegistry


class StreamingModel(FakeChatModel):
    release: object = None
    closed: bool = False
    fail: bool = False
    use_tools: bool = False

    def _generate(self, *args, **kwargs):
        raise AssertionError("Generation must consume the provider stream")

    def bind_tools(self, tools, **kwargs):
        return self

    async def _astream(self, messages, **kwargs):
        try:
            if self.use_tools and not isinstance(messages[-1], (ToolMessage, SystemMessage)):
                yield ChatGenerationChunk(message=AIMessageChunk(content="", tool_call_chunks=[
                    {"index": 0, "id": "call-1", "name": "lookup", "args": '{"query":'},
                ]))
                yield ChatGenerationChunk(message=AIMessageChunk(content="", tool_call_chunks=[
                    {"index": 0, "id": None, "name": None, "args": '"LangGraph"}'},
                ]))
                return
            yield ChatGenerationChunk(message=AIMessageChunk(content=[
                {"type": "reasoning", "reasoning": "private reasoning"},
                {"type": "text", "text": "## 结果\n\n"},
            ], additional_kwargs={"reasoning_content": "private provider trace"}))
            if self.release:
                await self.release.wait()
            if self.fail:
                raise RuntimeError("provider disconnected")
            for text in ["```python\n", "print('好')\n", "```"]:
                yield ChatGenerationChunk(message=AIMessageChunk(content=text))
        finally:
            self.closed = True


@pytest.mark.asyncio
async def test_first_text_arrives_before_model_completes_and_preserves_markdown():
    release, first = asyncio.Event(), asyncio.Event()
    model = StreamingModel(release=release)
    events = []

    async def emit(kind, payload):
        events.append((kind, payload))
        if kind == "model.output.delta":
            first.set()

    task = asyncio.create_task(build_graph(model, output_sink=emit).ainvoke({
        "messages": [HumanMessage(content="hello")],
    }))
    try:
        await asyncio.wait_for(first.wait(), 2)
        assert not task.done()
        assert events[1][1]["text"] == "## 结果\n\n"
        release.set()
        result = await task
        text = "".join(payload["text"] for kind, payload in events if kind == "model.output.delta")
        assert text == "## 结果\n\n```python\nprint('好')\n```"
        assert result["messages"][-1].text == text
        assert "private" not in str(events)
        assert model.closed
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_split_tool_arguments_execute_once_only_after_complete_aggregation():
    calls, events = [], []

    async def lookup(arguments):
        calls.append(arguments)
        return ToolResult.success("found")

    async def emit(kind, payload):
        events.append((kind, payload))

    registry = ToolRegistry([MemoryTool(
        "builtin.test.lookup", "lookup", "lookup",
        {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
        frozenset(), 1, lookup,
    )])
    result = await build_graph(
        StreamingModel(use_tools=True), tools=registry.function_schemas(),
        tool_executor=ToolExecutor(registry), tool_context=ToolExecutionContext("run", "agent"),
        output_sink=emit,
    ).ainvoke({"messages": [HumanMessage(content="lookup")]})
    assert calls == [{"query": "LangGraph"}]
    assert result["messages"][-1].text.startswith("## 结果")
    assert len([kind for kind, _ in events if kind == "model.output.started"]) == 2
    assert "LangGraph" not in str(events)
    assert "private" not in str(events)


@pytest.mark.asyncio
async def test_stream_is_closed_when_cancelled():
    first = asyncio.Event()
    model = StreamingModel(release=asyncio.Event())

    async def emit(kind, payload):
        if kind == "model.output.delta":
            first.set()

    task = asyncio.create_task(stream_model(model, [HumanMessage(content="hello")], emit=emit))
    try:
        await asyncio.wait_for(first.wait(), 2)
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert model.closed


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["completed", "cancelled", "failed"])
async def test_partial_output_is_durable_before_completion(postgres_factory, outcome):
    key = f"stream-{uuid4()}"
    events = asyncio.Queue()
    release = asyncio.Event()
    model = StreamingModel(release=release, fail=outcome == "failed")
    controller = RunController()

    async def chat():
        async with postgres_factory() as session:
            return await AgentService(
                session, chat_model=model, embedding_model=None, controller=controller,
                settings=Settings(_env_file=None, dashscope_api_key=""),
                business=BusinessConfig.model_validate({"tools": {"enabled": False}}),
            ).chat(user_key=key, content="hello", event_sink=events.put)

    task = asyncio.create_task(chat())
    try:
        while True:
            event = await asyncio.wait_for(events.get(), 5)
            if event["event_type"] == "model.output.delta":
                break
        run_id = UUID(event["run_id"])
        async with postgres_factory() as session:
            run = await session.get(Run, run_id)
            assert run.status == RunStatus.RUNNING
            durable = await session.scalar(select(RunEvent).where(
                RunEvent.run_id == run_id, RunEvent.sequence == event["sequence"],
            ))
            assert durable.payload["text"] == "## 结果\n\n"
            assert not await session.scalar(select(Message).where(
                Message.run_id == run_id, Message.role == MessageRole.ASSISTANT,
            ))
            owner = await session.scalar(select(User).where(User.external_key == key))
            owner_id = owner.id
        if outcome == "cancelled":
            assert controller.cancel(run_id, owner_id)
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            release.set()
            if outcome == "failed":
                with pytest.raises(RuntimeError, match="provider disconnected"):
                    await task
            else:
                response = await task
        async with postgres_factory() as session:
            run = await session.get(Run, run_id)
            assert run.status.value == outcome
            replay = list(await session.scalars(select(RunEvent).where(
                RunEvent.run_id == run_id,
            ).order_by(RunEvent.sequence)))
            assert replay[-1].event_type == f"run.{outcome}"
            assert [event.sequence for event in replay] == list(range(1, len(replay) + 1))
            assistants = list(await session.scalars(select(Message).where(
                Message.run_id == run_id, Message.role == MessageRole.ASSISTANT,
            )))
            if outcome == "completed":
                assert len(assistants) == 1
                streamed = "".join(event.payload["text"] for event in replay if event.event_type == "model.output.delta")
                assert assistants[0].content == response["message"] == streamed
            else:
                assert assistants == []
        assert model.closed
    finally:
        release.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        async with postgres_factory() as session:
            await session.execute(delete(User).where(User.external_key == key))
            await session.commit()
