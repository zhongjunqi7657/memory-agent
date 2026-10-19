from pathlib import Path
from typing import ClassVar

import pytest
from langchain_core.language_models.fake_chat_models import FakeChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langgraph.checkpoint.memory import InMemorySaver

from app.agent.graph import build_graph, replace_checkpoint_messages
from app.api.chat import format_sse
from app.config.business import BusinessConfig, business_config_path
from app.protocol.tools import ToolExecutionContext, ToolResult
from app.tools.builtin.memory import MemoryTool
from app.tools.executor import ToolExecutor
from app.tools.registry import ToolRegistry


class FixedChatModel(FakeChatModel):
    async def _agenerate(self, messages, **kwargs):
        return self._generate(messages, **kwargs)

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


async def lookup(arguments) -> ToolResult:
    """Look up a deterministic test value."""

    return ToolResult.success(f"找到：{arguments['query']}")


def tool_graph(model, **kwargs):
    registry = ToolRegistry([MemoryTool(
        "builtin.test.lookup", "lookup", "Look up a deterministic test value",
        {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
        frozenset(), 1, lookup,
    )])
    return build_graph(model, tools=registry.function_schemas(),
                       tool_executor=ToolExecutor(registry),
                       tool_context=ToolExecutionContext("run", "agent"), **kwargs)


def test_business_config_loads_toml(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        '[models]\nchat_model = "qwen-turbo"\nembedding_dimensions = 1024\n',
        encoding="utf-8",
    )
    config = BusinessConfig.load(config_path)
    assert config.models.chat_model == "qwen-turbo"
    assert config.models.embedding_dimensions == 1024


def test_business_config_runtime_path_override(tmp_path, monkeypatch):
    path = tmp_path / "deployment.toml"
    path.write_text('[tools]\ndefault_timeout_seconds = 2.5\n', encoding="utf-8")
    monkeypatch.setenv("MEMORY_AGENT_CONFIG", str(path))
    assert business_config_path() == path
    assert BusinessConfig.load().tools.default_timeout_seconds == 2.5


def test_explicit_new_tools_config_takes_precedence_over_legacy():
    with pytest.warns(DeprecationWarning):
        business = BusinessConfig.model_validate({
            "mcp": {"enabled": False, "allowed_tools": [], "tool_timeout_seconds": 3},
            "tools": {"enabled": True, "allowed_builtin_tools": ["builtin.memory.search"],
                      "default_timeout_seconds": 2},
        })
    assert business.tools.enabled
    assert business.tools.default_timeout_seconds == 2
    assert business.tools.allowed_builtin_tools == ["builtin.memory.search"]


def test_business_config_loads_mcp_policy(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        '[mcp]\nenabled = false\nallowed_tools = ["search_memory"]\n'
        'tool_timeout_seconds = 3.5\n',
        encoding="utf-8",
    )

    with pytest.warns(DeprecationWarning):
        config = BusinessConfig.load(config_path)

    assert config.mcp.enabled is False
    assert config.tools.enabled is False
    assert config.tools.allowed_builtin_tools == ["builtin.memory.search"]
    assert config.tools.default_timeout_seconds == 3.5


def test_graph_returns_model_message() -> None:
    graph = build_graph(FixedChatModel())
    result = graph.invoke({"messages": [HumanMessage(content="我想学 Python")]})
    assert result["messages"][-1].content == "收到，我会记住你的学习目标。"


@pytest.mark.asyncio
async def test_graph_runs_tool_node_and_returns_to_model() -> None:
    ToolLoopModel.call_count = 0
    model = ToolLoopModel()
    graph = tool_graph(model, max_tool_rounds=2)

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
    graph = tool_graph(model, max_tool_rounds=1)

    result = await graph.ainvoke(
        {
            "messages": [HumanMessage(content="一直调用工具")],
            "current_input": "一直调用工具",
        }
    )

    assert ToolLoopModel.call_count == 3
    assert "达到本轮上限" in result["messages"][-1].content


@pytest.mark.asyncio
async def test_tool_limit_summarizes_completed_results_without_executing_pending_calls():
    executed = []

    async def record_lookup(arguments):
        executed.append(arguments)
        return ToolResult.success("verified module evidence")

    class LimitSummaryModel(ToolLoopModel):
        async def _agenerate(self, messages, **kwargs):
            if messages[-1].type == "system":
                assert any(isinstance(item, ToolMessage) and "verified module evidence" in item.content for item in messages)
                assert messages[-2].status == "error"  # The extra requested call did not execute.
                return ChatResult(generations=[ChatGeneration(message=AIMessage(content="已检查模块存在验证证据；其他模块尚未检查。"))])
            return await super()._agenerate(messages, **kwargs)

    ToolLoopModel.call_count = 0
    registry = ToolRegistry([MemoryTool("builtin.test.lookup", "lookup", "Read test evidence",
                                       {"type": "object"}, frozenset(), 1, record_lookup)])
    graph = build_graph(LimitSummaryModel(always_call_tool=True), tools=registry.function_schemas(),
                        tool_executor=ToolExecutor(registry), tool_context=ToolExecutionContext("run", "agent"),
                        max_tool_rounds=1)
    result = await graph.ainvoke({"messages": [HumanMessage(content="审计项目")], "current_input": "审计项目"})
    assert len(executed) == 1
    assert "验证证据" in result["messages"][-1].content
    assert "尚未检查" in result["messages"][-1].content


@pytest.mark.asyncio
async def test_zero_tool_budget_does_not_execute_tools(monkeypatch):
    async def forbidden_lookup(arguments):
        pytest.fail("No tool may execute with a zero budget")

    monkeypatch.setattr("tests.test_config_and_graph.lookup", forbidden_lookup)
    ToolLoopModel.call_count = 0
    result = await tool_graph(ToolLoopModel(always_call_tool=True), max_tool_rounds=0).ainvoke({
        "messages": [HumanMessage(content="不要执行工具")], "current_input": "不要执行工具",
    })
    assert not result["messages"][-1].tool_calls
    assert "0" in result["messages"][-1].content


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
