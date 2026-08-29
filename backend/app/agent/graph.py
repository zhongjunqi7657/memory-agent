"""Small, testable LangGraph runtime for the first conversational loop."""

from __future__ import annotations

from typing import Annotated, TypedDict

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import BaseMessage, SystemMessage
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages


class AgentState(TypedDict, total=False):
    messages: Annotated[list[BaseMessage], add_messages]
    memory_context: str


SYSTEM_PROMPT = (
    "你是一个中文优先的个人成长与学习伙伴。"
    "只根据用户提供的事实回答，不把不确定推测说成事实。"
)


def build_graph(chat_model: BaseChatModel, *, system_prompt: str = SYSTEM_PROMPT):
    """Build a graph without hidden global state so it is easy to test or extend."""

    def call_model(state: AgentState) -> dict[str, list[BaseMessage]]:
        context = state.get("memory_context", "").strip()
        prompt = system_prompt
        if context:
            prompt = f"{system_prompt}\n\n可用的用户长期记忆：\n{context}"
        response = chat_model.invoke(
            [SystemMessage(content=prompt), *state["messages"]]
        )
        return {"messages": [response]}

    workflow = StateGraph(AgentState)
    workflow.add_node("call_model", call_model)
    workflow.add_edge(START, "call_model")
    workflow.add_edge("call_model", END)
    return workflow.compile()
