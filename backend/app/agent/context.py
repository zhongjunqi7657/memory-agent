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
    reserved_tokens: int
    summarized: bool
    truncated: bool


def estimate_tokens(text: str) -> int:
    """Conservative local estimate suitable for mixed Chinese and Latin text."""

    cjk_count = len(re.findall(r"[\u3400-\u9fff]", text))
    non_cjk = re.sub(r"[\u3400-\u9fff\s]", "", text)
    return max(1, cjk_count + math.ceil(len(non_cjk) / 4))


def _to_model_message(message: Message, *, content: str | None = None) -> BaseMessage:
    model_content = message.content if content is None else content
    if message.role is MessageRole.USER:
        return HumanMessage(content=model_content, id=str(message.id))
    return AIMessage(content=model_content, id=str(message.id))


def _truncate_to_token_budget(text: str, budget: int) -> str:
    if budget <= 0:
        return ""
    if estimate_tokens(text) <= budget:
        return text
    low = 0
    high = len(text)
    while low < high:
        midpoint = (low + high + 1) // 2
        if estimate_tokens(text[:midpoint]) <= budget:
            low = midpoint
        else:
            high = midpoint - 1
    return text[:low].rstrip()


def _select_within_budget(
    messages: list[Message], budget: int
) -> tuple[list[BaseMessage], int, bool]:
    selected: list[BaseMessage] = []
    used = 0
    for message in reversed(messages):
        cost = estimate_tokens(message.content) + 4
        if used + cost <= budget:
            selected.append(_to_model_message(message))
            used += cost
            continue
        remaining = budget - used - 4
        if not selected and remaining > 0:
            truncated = _truncate_to_token_budget(message.content, remaining)
            if truncated:
                selected.append(_to_model_message(message, content=truncated))
                used += estimate_tokens(truncated) + 4
            break
        break
    selected.reverse()
    return selected, used, len(selected) < len(messages) or any(
        str(model_message.content) != source.content
        for model_message, source in zip(selected, messages[-len(selected) :])
    )


def _select_summary_batch(
    messages: list[Message], budget: int
) -> tuple[list[tuple[Message, str]], bool]:
    selected: list[tuple[Message, str]] = []
    used = 0
    for message in messages:
        cost = estimate_tokens(message.content) + 4
        if used + cost <= budget:
            selected.append((message, message.content))
            used += cost
            continue
        remaining = budget - used - 4
        if not selected and remaining > 0:
            truncated = _truncate_to_token_budget(message.content, remaining)
            if truncated:
                selected.append((message, truncated))
        break
    return selected, len(selected) < len(messages) or any(
        content != message.content for message, content in selected
    )


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
        target_through_sequence = older[-1].sequence
        stale_by = target_through_sequence - conversation.summary_through_sequence
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
            previous = conversation.summary or "（暂无）"
            summary_overhead = (
                estimate_tokens(SUMMARY_PROMPT)
                + estimate_tokens(previous)
                + 20
            )
            summary_budget = max(0, config.context_token_budget - summary_overhead)
            summary_batch, _ = _select_summary_batch(new_older, summary_budget)
            if summary_batch:
                transcript = "\n".join(
                    f"{'用户' if item.role is MessageRole.USER else '助手'}：{content}"
                    for item, content in summary_batch
                )
                response = await chat_model.ainvoke(
                    [
                        SystemMessage(content=SUMMARY_PROMPT),
                        HumanMessage(
                            content=(
                                f"已有摘要：\n{previous}\n\n"
                                f"较早对话：\n{transcript}"
                            )
                        ),
                    ]
                )
                conversation.summary = str(response.content)[
                    : config.summary_max_chars
                ]
                conversation.summary_through_sequence = summary_batch[-1][0].sequence
                await session.flush()
                summarized = True

    stored_summary = conversation.summary or ""
    reserved_tokens = max(0, reserved_tokens)
    effective_reserved_tokens = min(
        reserved_tokens, config.context_token_budget
    )
    system_reserve_tokens = min(
        config.context_system_reserve_tokens,
        max(0, config.context_token_budget - effective_reserved_tokens),
    )
    available = max(
        0,
        config.context_token_budget
        - effective_reserved_tokens
        - system_reserve_tokens,
    )
    model_messages, message_tokens, messages_truncated = _select_within_budget(
        recent, available
    )
    summary_budget = max(0, available - message_tokens)
    summary = _truncate_to_token_budget(stored_summary, summary_budget)
    summary_truncated = summary != stored_summary
    estimated = (
        effective_reserved_tokens
        + system_reserve_tokens
        + message_tokens
        + (estimate_tokens(summary) if summary else 0)
    )
    return ConversationContext(
        messages=model_messages,
        summary=summary,
        estimated_tokens=estimated,
        reserved_tokens=effective_reserved_tokens,
        summarized=summarized,
        truncated=messages_truncated or summary_truncated,
    )
