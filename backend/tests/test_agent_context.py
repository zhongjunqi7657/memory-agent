from types import SimpleNamespace
from uuid import uuid4

import pytest
from langchain_core.messages import AIMessage

from app.agent.context import estimate_tokens, prepare_conversation_context
from app.config.business import AgentConfig
from app.persistence.models import MessageRole


class FakeSession:
    async def flush(self):
        return None


class SummaryModel:
    call_count = 0

    def __init__(self):
        self.messages = []

    async def ainvoke(self, messages):
        self.call_count += 1
        self.messages.append(messages)
        return AIMessage(content="用户在学习 LangGraph，偏好先理解原理。")


def message(sequence: int, role: MessageRole, content: str):
    return SimpleNamespace(
        id=uuid4(), sequence=sequence, role=role, content=content
    )


def test_token_estimate_counts_chinese_more_conservatively_than_latin() -> None:
    assert estimate_tokens("学习目标") == 4
    assert estimate_tokens("abcd") == 1


@pytest.mark.asyncio
async def test_long_conversation_updates_summary_and_keeps_recent_messages() -> None:
    conversation = SimpleNamespace(summary=None, summary_through_sequence=0)
    messages = [
        message(index, MessageRole.USER, f"第 {index} 轮学习记录" * 12)
        for index in range(1, 7)
    ]
    model = SummaryModel()

    context = await prepare_conversation_context(
        FakeSession(),
        conversation=conversation,
        messages=messages,
        chat_model=model,
        config=AgentConfig(
            recent_turns=2,
            context_token_budget=500,
            summary_trigger_tokens=200,
            summary_refresh_messages=2,
        ),
    )

    assert model.call_count == 1
    assert context.summarized is True
    assert context.summary.startswith("用户在学习 LangGraph")
    assert [item.content for item in context.messages] == [
        "第 5 轮学习记录" * 12,
        "第 6 轮学习记录" * 12,
    ]
    assert conversation.summary_through_sequence == 4


@pytest.mark.asyncio
async def test_short_conversation_does_not_trigger_summary() -> None:
    conversation = SimpleNamespace(summary=None, summary_through_sequence=0)
    messages = [
        message(index, MessageRole.USER, f"第 {index} 轮学习记录")
        for index in range(1, 4)
    ]
    model = SummaryModel()

    context = await prepare_conversation_context(
        FakeSession(),
        conversation=conversation,
        messages=messages,
        chat_model=model,
        config=AgentConfig(
            recent_turns=2,
            context_token_budget=500,
            summary_trigger_tokens=200,
        ),
    )

    assert model.call_count == 0
    assert context.summarized is False
    assert conversation.summary is None
    assert conversation.summary_through_sequence == 0


@pytest.mark.asyncio
async def test_summary_refresh_only_sends_messages_after_summary_cursor() -> None:
    conversation = SimpleNamespace(
        summary="用户已开始学习 LangGraph。", summary_through_sequence=2
    )
    messages = [
        message(index, MessageRole.USER, f"第 {index} 轮学习记录")
        for index in range(1, 7)
    ]
    model = SummaryModel()

    await prepare_conversation_context(
        FakeSession(),
        conversation=conversation,
        messages=messages,
        chat_model=model,
        config=AgentConfig(
            recent_turns=2,
            context_token_budget=500,
            summary_refresh_messages=2,
        ),
    )

    prompt = str(model.messages[0][1].content)
    assert "已有摘要：\n用户已开始学习 LangGraph。" in prompt
    assert "第 3 轮学习记录" in prompt
    assert "第 4 轮学习记录" in prompt
    assert "第 1 轮学习记录" not in prompt
    assert "第 2 轮学习记录" not in prompt
    assert conversation.summary_through_sequence == 4
