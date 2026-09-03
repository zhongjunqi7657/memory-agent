"""Application service coordinating persistence, memory context and LangGraph."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from uuid import UUID

from langchain_core.messages import AIMessage, HumanMessage
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.graph import build_graph
from app.config.business import BusinessConfig, get_business_config
from app.config.settings import Settings, get_settings
from app.memory.commands import MemoryCommandType, parse_memory_command
from app.memory.service import MemoryService
from app.models.qwen import create_chat_model, create_embedding_model
from app.persistence.models import MemoryStatus, MessageRole, RunStatus
from app.persistence.repositories import ConversationRepository, MemoryRepository
from app.security.redaction import redact_secrets


class AgentInputError(ValueError):
    """Raised when a chat request violates a user-visible input limit."""


EventSink = Callable[[dict[str, object]], Awaitable[None]]
_AUTO_EMBEDDING = object()


class AgentService:
    def __init__(
        self,
        session: AsyncSession,
        *,
        chat_model=None,
        embedding_model=_AUTO_EMBEDDING,
        settings: Settings | None = None,
        business: BusinessConfig | None = None,
    ):
        self.session = session
        self.settings = settings or get_settings()
        self.business = business or get_business_config()
        self.chat_model = chat_model
        self.embedding_model = None
        if embedding_model is _AUTO_EMBEDDING and self.settings.dashscope_api_key:
            self.embedding_model = create_embedding_model(
                settings=self.settings, config=self.business
            )
        elif embedding_model is not _AUTO_EMBEDDING:
            self.embedding_model = embedding_model

    async def chat(
        self,
        *,
        user_key: str,
        content: str,
        conversation_id: UUID | None = None,
        event_sink: EventSink | None = None,
    ) -> dict[str, object]:
        if not content.strip():
            raise AgentInputError("消息不能为空")
        if len(content) > self.business.security.max_input_chars:
            raise AgentInputError("消息超过长度限制")

        redaction = redact_secrets(content)
        repository = ConversationRepository(self.session)
        user = await repository.get_or_create_user(user_key)
        conversation = None
        if conversation_id:
            conversation = await repository.get_conversation(conversation_id, user.id)
            if conversation is None:
                raise AgentInputError("会话不存在或不属于当前用户")
        if conversation is None:
            conversation = await repository.create_conversation(user.id)

        sequence = await repository.next_message_sequence(conversation.id)
        user_message = await repository.add_message(
            conversation.id,
            role=MessageRole.USER,
            content=redaction.text,
            sequence=sequence,
            is_redacted=redaction.redacted,
        )
        run = await repository.create_run(conversation.id)
        event_sequence = 0

        async def emit(
            event_type: str, payload: dict[str, object] | None = None
        ) -> None:
            nonlocal event_sequence
            event_sequence += 1
            event_payload = payload or {}
            await repository.add_run_event(
                run.id,
                sequence=event_sequence,
                event_type=event_type,
                payload=event_payload,
            )
            if event_sink:
                await event_sink(
                    {
                        "run_id": str(run.id),
                        "sequence": event_sequence,
                        "event_type": event_type,
                        "payload": event_payload,
                    }
                )

        async def finish_success(
            answer: str,
            *,
            memory_count: int,
            queue_extraction: bool,
            memory_command: str | None = None,
        ) -> dict[str, object]:
            assistant_sequence = await repository.next_message_sequence(conversation.id)
            await repository.add_message(
                conversation.id,
                role=MessageRole.ASSISTANT,
                content=answer,
                sequence=assistant_sequence,
            )
            if queue_extraction:
                await repository.queue_extraction_job(
                    user.id, conversation.id, user_message.id
                )
                await emit("memory.extraction_queued")
            await repository.finish_run(run, status=RunStatus.COMPLETED)
            await emit(
                "run.completed",
                {
                    "conversation_id": str(conversation.id),
                    "run_id": str(run.id),
                    "message": answer,
                    "redacted": redaction.redacted,
                    "redaction_categories": list(redaction.categories),
                    "memory_count": memory_count,
                    "memory_command": memory_command,
                },
            )
            await self.session.commit()
            return {
                "conversation_id": conversation.id,
                "run_id": run.id,
                "message": answer,
                "redacted": redaction.redacted,
                "redaction_categories": redaction.categories,
                "memory_count": memory_count,
                "memory_command": memory_command,
                "created_at": datetime.now(timezone.utc),
            }

        async def finish_failure(error: Exception) -> None:
            safe_error = redact_secrets(str(error)).text[:500]
            await repository.finish_run(
                run, status=RunStatus.FAILED, error_message=safe_error
            )
            await emit("run.failed", {"message": "模型运行失败"})
            await self.session.commit()

        await emit("run.started", {"conversation_id": str(conversation.id)})
        command = parse_memory_command(redaction.text)
        if command:
            try:
                memory_service = MemoryService(self.session, business=self.business)
                response_memory_count = 0
                if command.type is MemoryCommandType.SAVE:
                    if redaction.redacted:
                        answer = "出于安全考虑，包含凭据的内容不会写入长期记忆。"
                        memory_count = 0
                        outcome = "rejected_secret"
                    else:
                        memory = await memory_service.add_explicit(
                            user_id=user.id,
                            content=command.target,
                            source_message_id=user_message.id,
                        )
                        answer = "这条内容没有被写入长期记忆。"
                        if memory and memory.status is MemoryStatus.ACTIVE:
                            answer = f"已记住：{memory.content}"
                        elif memory:
                            answer = "这条信息已加入待确认列表，不会直接用于后续回答。"
                        memory_count = 1 if memory else 0
                        outcome = (
                            memory.status.value if memory else "rejected"
                        )
                elif command.type is MemoryCommandType.FORGET:
                    deleted = await memory_service.forget(
                        user_id=user.id, query=command.target
                    )
                    answer = (
                        f"已删除 {len(deleted)} 条相关长期记忆。"
                        if deleted
                        else "没有找到匹配的长期记忆。"
                    )
                    memory_count = 0
                    outcome = "deleted" if deleted else "not_found"
                elif command.type is MemoryCommandType.LIST:
                    active_memories = await memory_service.list_active(user_id=user.id)
                    if active_memories:
                        answer = "我目前记住了：\n" + "\n".join(
                            f"- {memory.content}" for memory in active_memories
                        )
                    else:
                        answer = "目前还没有已生效的长期记忆。"
                    memory_count = 0
                    response_memory_count = len(active_memories)
                    outcome = "listed"
                else:
                    new_memory, superseded = await memory_service.correct(
                        user_id=user.id,
                        old_query=command.target,
                        replacement=command.replacement or "",
                        source_message_id=user_message.id,
                    )
                    answer = "这条更正没有被写入长期记忆。"
                    if new_memory and new_memory.status is MemoryStatus.ACTIVE:
                        answer = f"已更新记忆：{new_memory.content}"
                    elif new_memory:
                        answer = "这条更正已加入待确认列表，旧记忆暂时保持有效。"
                    memory_count = 1 if new_memory else 0
                    outcome = (
                        f"corrected_{new_memory.status.value}"
                        if new_memory
                        else "rejected"
                    )
                    if superseded:
                        outcome = f"{outcome}_{len(superseded)}_superseded"
                await emit(
                    "memory.command_applied",
                    {
                        "command": command.type.value,
                        "outcome": outcome,
                        "changed_count": memory_count,
                    },
                )
                return await finish_success(
                    answer,
                    memory_count=response_memory_count,
                    queue_extraction=False,
                    memory_command=command.type.value,
                )
            except Exception as error:
                await finish_failure(error)
                raise

        query_embedding = None
        if self.embedding_model:
            try:
                query_embedding = await self.embedding_model.aembed_query(redaction.text)
                await emit(
                    "memory.embedding_ready", {"dimensions": len(query_embedding)}
                )
            except Exception:  # noqa: BLE001 - retrieval must fall back safely
                await emit("memory.embedding_fallback")
        scored_memories = await MemoryRepository(self.session).search_hybrid(
            user.id,
            redaction.text,
            query_embedding=query_embedding,
            limit=self.business.memory.auto_retrieve_limit,
            vector_weight=self.business.memory.vector_weight,
            keyword_weight=self.business.memory.keyword_weight,
        )
        memories = [item.memory for item in scored_memories]
        memory_context = "\n".join(f"- {memory.content}" for memory in memories)
        await emit("memory.retrieved", {"count": len(memories)})
        recent_messages = await repository.list_recent_messages(
            conversation.id, self.business.agent.recent_turns
        )
        graph_messages = [
            HumanMessage(content=message.content)
            if message.role is MessageRole.USER
            else AIMessage(content=message.content)
            for message in recent_messages
        ]

        try:
            chat_model = self.chat_model or create_chat_model(
                settings=self.settings, config=self.business
            )
            result = await build_graph(chat_model).ainvoke(
                {"messages": graph_messages, "memory_context": memory_context}
            )
            answer = str(result["messages"][-1].content)
            await emit("model.completed", {"answer_length": len(answer)})
            return await finish_success(
                answer, memory_count=len(memories), queue_extraction=True
            )
        except Exception as error:
            await finish_failure(error)
            raise
