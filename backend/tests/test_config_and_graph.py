from pathlib import Path
from typing import ClassVar

import pytest
from langchain_core.language_models.fake_chat_models import FakeChatModel
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.tools import tool
from langgraph.checkpoint.memory import InMemorySaver

from app.agent.graph import build_graph, replace_checkpoint_messages
from app.api.chat import format_sse
from app.config.business import BusinessConfig


class FixedChatModel(FakeChatModel):
    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        return ChatResult(
            generations=[
                ChatGeneration(
                    message=AIMessage(content="收到，我会记住你的学习目标。")
                )
            ]
        )


class ToolLoopModel(FakeChatModel):
    call_count: ClassVar[int] = 0
    always_call_tool: bool = False

    def bind_tools(self, _tools, **_kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        type(self).call_count += 1
        if type(self).call_count == 1 or self.always_call_tool:
            message = AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "lookup",
                        "args": {"query": "LangGraph"},
                        "id": f"call-{type(self).call_count}",
                        "type": "tool_call",
                    }
                ],
            )
        else:
            message = AIMessage(content="已经根据工具结果完成回答。")
        return ChatResult(generations=[ChatGeneration(message=message)])

    async def _agenerate(self, messages, stop=None, run_manager=None, **kwargs):
        return self._generate(messages, stop=stop, run_manager=run_manager, **kwargs)


@tool
def lookup(query: str) -> str:
    """Look up a deterministic test value."""

    return f"找到：{query}"


def test_business_config_loads_toml(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        '[models]\nchat_model = "qwen-turbo"\nembedding_dimensions = 1024\n',
        encoding="utf-8",
    )
    config = BusinessConfig.load(config_path)
    assert config.models.chat_model == "qwen-turbo"
    assert config.models.embedding_dimensions == 1024


def test_graph_returns_model_message() -> None:
    graph = build_graph(FixedChatModel())
    result = graph.invoke({"messages": [HumanMessage(content="我想学 Python")]})
    assert result["messages"][-1].content == "收到，我会记住你的学习目标。"


@pytest.mark.asyncio
async def test_graph_runs_tool_node_and_returns_to_model() -> None:
    ToolLoopModel.call_count = 0
    model = ToolLoopModel()
    graph = build_graph(model, tools=[lookup], max_tool_rounds=2)

    result = await graph.ainvoke(
        {
            "messages": [HumanMessage(content="查一下 LangGraph")],
            "current_input": "查一下 LangGraph",
        }
    )

    assert ToolLoopModel.call_count == 2
    assert result["messages"][-1].content == "已经根据工具结果完成回答。"
    assert any(message.type == "tool" for message in result["messages"])


@pytest.mark.asyncio
async def test_graph_stops_after_configured_tool_round_limit() -> None:
    ToolLoopModel.call_count = 0
    model = ToolLoopModel(always_call_tool=True)
    graph = build_graph(model, tools=[lookup], max_tool_rounds=1)

    result = await graph.ainvoke(
        {
            "messages": [HumanMessage(content="一直调用工具")],
            "current_input": "一直调用工具",
        }
    )

    assert ToolLoopModel.call_count == 2
    assert "达到本轮上限" in result["messages"][-1].content


def test_explicit_memory_command_bypasses_normal_model_path() -> None:
    ToolLoopModel.call_count = 0
    model = ToolLoopModel()
    result = build_graph(model).invoke(
        {
            "messages": [HumanMessage(content="请记住我喜欢先看原理")],
            "current_input": "请记住我喜欢先看原理",
        }
    )

    assert result["memory_command"] == "save"
    assert ToolLoopModel.call_count == 0


@pytest.mark.asyncio
async def test_graph_writes_checkpoint_by_conversation_thread() -> None:
    saver = InMemorySaver()
    graph = build_graph(FixedChatModel(), checkpointer=saver)
    config = {"configurable": {"thread_id": "conversation-1"}}

    await graph.ainvoke(
        {
            "messages": [HumanMessage(content="记录当前进度")],
            "current_input": "记录当前进度",
        },
        config=config,
    )

    checkpoint = await saver.aget_tuple(config)
    assert checkpoint is not None
    assert checkpoint.config["configurable"]["thread_id"] == "conversation-1"


@pytest.mark.asyncio
async def test_checkpoint_context_is_replaced_between_turns() -> None:
    saver = InMemorySaver()
    graph = build_graph(FixedChatModel(), checkpointer=saver)
    config = {"configurable": {"thread_id": "conversation-1"}}

    await graph.ainvoke(
        {
            "messages": replace_checkpoint_messages(
                [HumanMessage(content="第一轮", id="user-1")]
            ),
            "current_input": "第一轮",
        },
        config=config,
    )
    result = await graph.ainvoke(
        {
            "messages": replace_checkpoint_messages(
                [HumanMessage(content="第二轮", id="user-2")]
            ),
            "current_input": "第二轮",
        },
        config=config,
    )

    assert [message.content for message in result["messages"]] == [
        "第二轮",
        "收到，我会记住你的学习目标。",
    ]


def test_format_sse_keeps_structured_event_data() -> None:
    encoded = format_sse(
        {"event_type": "memory.retrieved", "sequence": 2, "payload": {"count": 3}}
    )
    assert encoded.startswith("event: memory.retrieved\n")
    assert '"count": 3' in encoded
    assert encoded.endswith("\n\n")
