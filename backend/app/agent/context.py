"""Conversation summary and token-budget handling for model input."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.business import AgentConfig
from app.persistence.models import Conversation, Message, MessageRole

SUMMARY_PROMPT = """请把较早的对话压缩为供后续对话使用的简洁中文摘要。
保留用户明确表达的目标、偏好、进展、未解决问题和重要决定；不要把助手推测写成用户事实。
不要输出标题、过程解释或列表之外的附加说明。"""


@dataclass(frozen=True)
class ConversationContext:
    messages: list[BaseMessage]
    summary: str
    estimated_tokens: int
    summarized: bool


def estimate_tokens(text: str) -> int:
    """Conservative local estimate suitable for mixed Chinese and Latin text."""

    cjk_count = len(re.findall(r"[\u3400-\u9fff]", text))
    non_cjk = re.sub(r"[\u3400-\u9fff\s]", "", text)
    return max(1, cjk_count + math.ceil(len(non_cjk) / 4))


def _to_model_message(message: Message) -> BaseMessage:
    if message.role is MessageRole.USER:
        return HumanMessage(content=message.content, id=str(message.id))
    return AIMessage(content=message.content, id=str(message.id))


def _select_within_budget(messages: list[Message], budget: int) -> list[Message]:
    selected: list[Message] = []
    used = 0
    for message in reversed(messages):
        cost = estimate_tokens(message.content) + 4
        if selected and used + cost > budget:
            break
        selected.append(message)
        used += cost
    return list(reversed(selected))


async def prepare_conversation_context(
    session: AsyncSession,
    *,
    conversation: Conversation,
    messages: list[Message],
    chat_model,
    config: AgentConfig,
    reserved_tokens: int = 0,
) -> ConversationContext:
    recent_limit = max(1, config.recent_turns)
    recent = messages[-recent_limit:]
    older = messages[:-recent_limit]
    summarized = False
    total_tokens = sum(estimate_tokens(item.content) + 4 for item in messages)

    if older:
        through_sequence = older[-1].sequence
        stale_by = through_sequence - conversation.summary_through_sequence
        should_refresh = (
            (not conversation.summary and total_tokens >= config.summary_trigger_tokens)
            or (
                bool(conversation.summary)
                and stale_by >= config.summary_refresh_messages
            )
        )
        if should_refresh:
            new_older = [
                item
                for item in older
                if item.sequence > conversation.summary_through_sequence
            ]
            transcript = "\n".join(
                f"{'用户' if item.role is MessageRole.USER else '助手'}：{item.content}"
                for item in new_older
            )
            previous = conversation.summary or "（暂无）"
            response = await chat_model.ainvoke(
                [
                    SystemMessage(content=SUMMARY_PROMPT),
                    HumanMessage(
                        content=f"已有摘要：\n{previous}\n\n较早对话：\n{transcript}"
                    ),
                ]
            )
            conversation.summary = str(response.content)[: config.summary_max_chars]
            conversation.summary_through_sequence = through_sequence
            await session.flush()
            summarized = True

    summary = conversation.summary or ""
    available = max(
        100,
        config.context_token_budget
        - reserved_tokens
        - estimate_tokens(summary)
        - 200,
    )
    selected = _select_within_budget(recent, available)
    model_messages = [_to_model_message(item) for item in selected]
    estimated = sum(estimate_tokens(str(item.content)) + 4 for item in model_messages)
    estimated += estimate_tokens(summary) if summary else 0
    return ConversationContext(
        messages=model_messages,
        summary=summary,
        estimated_tokens=estimated,
        summarized=summarized,
    )
