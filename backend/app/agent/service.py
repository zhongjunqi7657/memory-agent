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
from app.models.qwen import create_chat_model, create_embedding_model
from app.persistence.models import MessageRole, RunStatus
from app.persistence.repositories import ConversationRepository, MemoryRepository
from app.security.redaction import redact_secrets


class AgentInputError(ValueError):
    """Raised when a chat request violates a user-visible input limit."""


EventSink = Callable[[dict[str, object]], Awaitable[None]]


class AgentService:
    def __init__(
        self,
        session: AsyncSession,
        *,
        chat_model=None,
        embedding_model=None,
        settings: Settings | None = None,
        business: BusinessConfig | None = None,
    ):
        self.session = session
        self.settings = settings or get_settings()
        self.business = business or get_business_config()
        self.chat_model = chat_model or create_chat_model(
            settings=self.settings, config=self.business
        )
        self.embedding_model = embedding_model
        if self.embedding_model is None and self.settings.dashscope_api_key:
            self.embedding_model = create_embedding_model(
                settings=self.settings, config=self.business
            )

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

        await emit("run.started", {"conversation_id": str(conversation.id)})
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
            result = await build_graph(self.chat_model).ainvoke(
                {"messages": graph_messages, "memory_context": memory_context}
            )
            answer = str(result["messages"][-1].content)
            await emit("model.completed", {"answer_length": len(answer)})
            assistant_sequence = await repository.next_message_sequence(conversation.id)
            await repository.add_message(
                conversation.id,
                role=MessageRole.ASSISTANT,
                content=answer,
                sequence=assistant_sequence,
            )
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
                    "memory_count": len(memories),
                },
            )
            await self.session.commit()
        except Exception as error:
            safe_error = redact_secrets(str(error)).text[:500]
            await repository.finish_run(
                run, status=RunStatus.FAILED, error_message=safe_error
            )
            await emit("run.failed", {"message": "模型运行失败"})
            await self.session.commit()
            raise

        return {
            "conversation_id": conversation.id,
            "run_id": run.id,
            "message": answer,
            "redacted": redaction.redacted,
            "redaction_categories": redaction.categories,
            "memory_count": len(memories),
            "created_at": datetime.now(timezone.utc),
        }
