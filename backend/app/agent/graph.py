"""LangGraph conversation loop with explicit routing and bounded memory tools."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Annotated, TypedDict

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, RemoveMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import REMOVE_ALL_MESSAGES, add_messages
from langgraph.prebuilt import ToolNode

from app.memory.commands import parse_memory_command


class AgentState(TypedDict, total=False):
    messages: Annotated[list[BaseMessage], add_messages]
    current_input: str
    conversation_summary: str
    memory_context: str
    retrieved_memories: list[dict[str, object]]
    memory_command: str | None
    tool_rounds: int


SYSTEM_PROMPT = (
    "你是一个中文优先的个人成长与学习伙伴。"
    "只根据用户提供的事实回答，不把不确定推测说成事实。"
    "自动提供的长期记忆不足时，可以使用记忆检索或时间线工具。"
    "任何记忆变更都只能提出待确认建议，不能声称已经直接生效。"
)


def replace_checkpoint_messages(
    messages: Sequence[BaseMessage],
) -> list[BaseMessage]:
    """Replace persisted graph messages with the database-budgeted context."""

    return [RemoveMessage(id=REMOVE_ALL_MESSAGES), *messages]


def _system_message(state: AgentState, system_prompt: str) -> SystemMessage:
    sections = [system_prompt]
    summary = state.get("conversation_summary", "").strip()
    if summary:
        sections.append(f"当前会话摘要：\n{summary}")
    context = state.get("memory_context", "").strip()
    if context:
        sections.append(f"可用的用户长期记忆：\n{context}")
    return SystemMessage(content="\n\n".join(sections))


def build_graph(
    chat_model: BaseChatModel,
    *,
    tools: Sequence[BaseTool] = (),
    max_tool_rounds: int = 4,
    checkpointer: BaseCheckpointSaver | None = None,
    system_prompt: str = SYSTEM_PROMPT,
):
    """Build the graph without database access inside orchestration nodes."""

    def load_session(state: AgentState) -> dict[str, object]:
        return {"tool_rounds": state.get("tool_rounds", 0)}

    def classify_explicit_intent(state: AgentState) -> dict[str, object]:
        content = state.get("current_input", "")
        command = parse_memory_command(content) if content else None
        return {"memory_command": command.type.value if command else None}

    def route_intent(state: AgentState) -> str:
        return "command" if state.get("memory_command") else "normal"

    def retrieve_memory(state: AgentState) -> dict[str, object]:
        if state.get("memory_context") or not state.get("retrieved_memories"):
            return {}
        lines = [
            f"- {item['content']}"
            for item in state["retrieved_memories"]
            if item.get("content")
        ]
        return {"memory_context": "\n".join(lines)}

    workflow = StateGraph(AgentState)
    workflow.add_node("load_session", load_session)
    workflow.add_node("classify_explicit_intent", classify_explicit_intent)
    workflow.add_node("retrieve_memory", retrieve_memory)
    workflow.add_edge(START, "load_session")
    workflow.add_edge("load_session", "classify_explicit_intent")
    workflow.add_conditional_edges(
        "classify_explicit_intent",
        route_intent,
        {"command": END, "normal": "retrieve_memory"},
    )

    if not tools:
        def call_model(state: AgentState) -> dict[str, list[BaseMessage]]:
            response = chat_model.invoke(
                [_system_message(state, system_prompt), *state["messages"]]
            )
            return {"messages": [response]}

        workflow.add_node("call_model", call_model)
        workflow.add_edge("retrieve_memory", "call_model")
        workflow.add_edge("call_model", END)
        return workflow.compile(checkpointer=checkpointer)

    try:
        model_with_tools = chat_model.bind_tools(list(tools))
    except NotImplementedError:
        return build_graph(
            chat_model,
            max_tool_rounds=max_tool_rounds,
            checkpointer=checkpointer,
            system_prompt=system_prompt,
        )
    tool_node = ToolNode(list(tools))

    async def call_model_with_tools(
        state: AgentState,
    ) -> dict[str, list[BaseMessage]]:
        response = await model_with_tools.ainvoke(
            [_system_message(state, system_prompt), *state["messages"]]
        )
        return {"messages": [response]}

    async def execute_tools(
        state: AgentState, config: RunnableConfig
    ) -> dict[str, object]:
        result = await tool_node.ainvoke(state, config)
        return {
            "messages": result["messages"],
            "tool_rounds": state.get("tool_rounds", 0) + 1,
        }

    def route_model(state: AgentState) -> str:
        last_message = state["messages"][-1]
        tool_calls = getattr(last_message, "tool_calls", [])
        if not tool_calls:
            return "done"
        if state.get("tool_rounds", 0) >= max_tool_rounds:
            return "limit"
        return "tools"

    def tool_limit(_state: AgentState) -> dict[str, list[BaseMessage]]:
        return {
            "messages": [
                AIMessage(
                    content="工具调用已达到本轮上限。请缩小问题范围后再试。"
                )
            ]
        }

    workflow.add_node("call_model", call_model_with_tools)
    workflow.add_node("tools", execute_tools)
    workflow.add_node("tool_limit", tool_limit)
    workflow.add_edge("retrieve_memory", "call_model")
    workflow.add_conditional_edges(
        "call_model",
        route_model,
        {"tools": "tools", "limit": "tool_limit", "done": END},
    )
    workflow.add_edge("tools", "call_model")
    workflow.add_edge("tool_limit", END)
    return workflow.compile(checkpointer=checkpointer)
