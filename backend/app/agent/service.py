"""Application service coordinating persistence, memory context and LangGraph."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from langchain_core.messages import AIMessage, HumanMessage
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.graph import build_graph
from app.config.business import BusinessConfig, get_business_config
from app.config.settings import Settings, get_settings
from app.models.qwen import create_chat_model
from app.persistence.models import MessageRole, RunStatus
from app.persistence.repositories import ConversationRepository, MemoryRepository
from app.security.redaction import redact_secrets


class AgentInputError(ValueError):
    """Raised when a chat request violates a user-visible input limit."""


class AgentService:
    def __init__(
        self,
        session: AsyncSession,
        *,
        chat_model=None,
        settings: Settings | None = None,
        business: BusinessConfig | None = None,
    ):
        self.session = session
        self.settings = settings or get_settings()
        self.business = business or get_business_config()
        self.chat_model = chat_model or create_chat_model(
            settings=self.settings, config=self.business
        )

    async def chat(
        self, *, user_key: str, content: str, conversation_id: UUID | None = None
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
        memories = await MemoryRepository(self.session).search_keyword(
            user.id,
            redaction.text,
            limit=self.business.memory.auto_retrieve_limit,
        )
        memory_context = "\n".join(f"- {memory.content}" for memory in memories)
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
            await repository.finish_run(run, status=RunStatus.COMPLETED)
            await self.session.commit()
        except Exception as error:
            await repository.finish_run(
                run, status=RunStatus.FAILED, error_message=str(error)[:500]
            )
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
