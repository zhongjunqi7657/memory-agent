"""Application service coordinating persistence, memory context and LangGraph."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Literal
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.checkpoint import get_checkpointer
from app.agent.context import estimate_tokens, prepare_conversation_context
from app.agent.graph import SYSTEM_PROMPT, build_graph, replace_checkpoint_messages
from app.agent.project_context import project_context
from app.audit.sink import CallbackAuditSink
from app.config.business import BusinessConfig, get_business_config
from app.config.settings import Settings, get_settings
from app.execution.controller import RunConflict, RunController, default_controller
from app.execution.lifecycle import EventSink, RunLifecycle
from app.memory.commands import MemoryCommandType, parse_memory_command
from app.memory.embeddings import embed_text, embedding_fallback_payload
from app.memory.service import MemoryService
from app.models.qwen import create_chat_model, create_embedding_model
from app.permission.approvals import ApprovalBroker, default_broker
from app.persistence.models import MemoryStatus, MessageRole, RunStatus
from app.persistence.projects import Project
from app.persistence.repositories import ConversationRepository, MemoryRepository
from app.protocol.tools import ToolExecutionContext
from app.security.redaction import redact_secrets
from app.tools.executor import ToolExecutor
from app.tools.local.files import LocalFileError
from app.tools.request import build_request_registry, request_permissions
from app.workspace.journal import ChangeJournal
from app.workspace.paths import DirectoryError
from app.workspace.service import project_directory
from app.workspace.worktrees import execution_worktree


class AgentInputError(ValueError):
    """Raised when a chat request violates a user-visible input limit."""


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
        mcp_manager=None,
        controller: RunController | None = None,
        broker: ApprovalBroker | None = None,
    ):
        self.session = session
        self.settings = settings or get_settings()
        self.business = business or get_business_config()
        self.chat_model = chat_model
        self.mcp_manager = mcp_manager
        self.controller = controller or default_controller
        self.broker = broker or default_broker
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
        project_id: UUID | None = None,
        execution_mode: Literal["original", "worktree"] = "original",
        worktree_id: UUID | None = None,
    ) -> dict[str, object]:
        lifecycle = RunLifecycle(self.session, self.controller, event_sink)
        try:
            return await self._chat(
                user_key=user_key, content=content, conversation_id=conversation_id,
                project_id=project_id, execution_mode=execution_mode, worktree_id=worktree_id, lifecycle=lifecycle,
            )
        except (DirectoryError, RunConflict, LocalFileError) as error:
            if lifecycle.registered:
                await lifecycle.terminate(RunStatus.FAILED, error)
            raise AgentInputError(str(error)) from error
        except asyncio.CancelledError as error:
            if lifecycle.registered:
                await lifecycle.terminate(RunStatus.CANCELLED, error)
            raise
        except Exception as error:
            if lifecycle.registered:
                await lifecycle.terminate(RunStatus.FAILED, error)
            raise
        finally:
            self.controller.release(lifecycle.run_id)

    async def _chat(
        self, *, user_key: str, content: str, conversation_id: UUID | None,
        project_id: UUID | None, execution_mode: str, worktree_id: UUID | None, lifecycle: RunLifecycle,
    ) -> dict[str, object]:
        if not content.strip():
            raise AgentInputError("消息不能为空")
        if len(content) > self.business.security.max_input_chars:
            raise AgentInputError("消息超过长度限制")
        if execution_mode not in {"original", "worktree"} or (execution_mode == "original" and worktree_id is not None):
            raise AgentInputError("执行模式与 worktree 选择不一致")
        if execution_mode == "worktree" and (worktree_id is None or not self.business.local.enabled):
            raise AgentInputError("worktree 模式需要启用本地执行并显式选择已创建的 worktree")

        redaction = redact_secrets(content)
        repository = ConversationRepository(self.session)
        user = await repository.get_or_create_user(user_key)
        conversation = None
        if conversation_id:
            conversation = await repository.get_conversation(conversation_id, user.id)
            if conversation is None:
                raise AgentInputError("会话不存在或不属于当前用户")
            if project_id is not None and conversation.project_id != project_id:
                raise AgentInputError("会话已绑定其他项目，请新建会话")
            project_id = conversation.project_id
        directory = await project_directory(self.session, user.id, project_id)
        project = await self.session.get(Project, project_id) if project_id else None
        if execution_mode == "worktree":
            if project_id is None:
                raise AgentInputError("worktree 必须绑定项目")
            directory = await execution_worktree(self.session, user.id, project, worktree_id, self.business.local)
        if conversation is None:
            conversation = await repository.create_conversation(
                user.id, title=redaction.text.strip()[:60]
            )
            conversation.project_id = project_id

        lifecycle.reserve(user.id, conversation.id, directory)

        sequence = await repository.next_message_sequence(conversation.id)
        user_message = await repository.add_message(
            conversation.id,
            role=MessageRole.USER,
            content=redaction.text,
            sequence=sequence,
            is_redacted=redaction.redacted,
        )
        run = await repository.create_run(
            conversation.id, id=lifecycle.run_id, project_id=project_id,
            execution_mode=execution_mode, execution_directory=directory,
        )
        run.worktree_id = worktree_id
        emit = lifecycle.emit

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
                run_id=run.id,
            )
            if queue_extraction:
                await repository.queue_extraction_job(
                    user.id,
                    conversation.id,
                    user_message.id,
                    payload={"run_id": str(run.id)},
                )
                await emit("memory.extraction_queued", durable=False)
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
            await lifecycle.terminate(RunStatus.FAILED, error)

        await emit("run.started", {"conversation_id": str(conversation.id)})
        local_available = self.business.local.enabled and self.business.tools.enabled
        journal = await ChangeJournal.start(self.session, run, self.business.local) if directory and local_available else None
        command = parse_memory_command(redaction.text)
        if command:
            try:
                memory_service = MemoryService(self.session, business=self.business, project_id=project_id)
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
                            conversation_id=conversation.id,
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
                        conversation_id=conversation.id,
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

        workspace_context = project_context(project, directory, execution_mode, local_available=local_available)
        workspace_tokens = estimate_tokens(workspace_context) if workspace_context else 0
        embedding_attempt = await embed_text(
            self.embedding_model,
            redaction.text,
            expected_dimensions=self.business.models.embedding_dimensions,
        )
        query_embedding = embedding_attempt.vector
        if embedding_attempt.ready:
            await emit(
                "memory.embedding_ready",
                {
                    "dimensions": len(query_embedding or []),
                    "model": self.business.models.embedding_model,
                },
            )
        else:
            await emit(
                "memory.embedding_fallback",
                embedding_fallback_payload(
                    embedding_attempt,
                    expected_dimensions=self.business.models.embedding_dimensions,
                    source="automatic_retrieval",
                ),
            )
        scored_memories = await MemoryRepository(self.session, project_id=project_id, include_personal=True).search_hybrid(
            user.id,
            redaction.text,
            query_embedding=query_embedding,
            limit=self.business.memory.auto_retrieve_limit,
            vector_weight=self.business.memory.vector_weight,
            keyword_weight=self.business.memory.keyword_weight,
            recency_weight=self.business.memory.recency_weight,
            importance_weight=self.business.memory.importance_weight,
            type_weight=self.business.memory.type_weight,
            recency_half_life_days=self.business.memory.recency_half_life_days,
            min_relevance_score=self.business.memory.min_relevance_score,
            embedding_model_name=self.business.models.embedding_model,
            embedding_dimensions=self.business.models.embedding_dimensions,
        )
        injected: list = []
        injected_tokens = 0
        current_input_tokens = estimate_tokens(redaction.text) + 4
        injection_budget = max(
            0,
            min(
                self.business.memory.max_injected_tokens,
                self.business.agent.context_token_budget
                - self.business.agent.context_system_reserve_tokens
                - workspace_tokens
                - current_input_tokens,
            ),
        )
        for item in scored_memories:
            item_tokens = estimate_tokens(f"[{'项目' if item.memory.project_id else '个人'}] {item.memory.content}") + 4
            if injected_tokens + item_tokens > injection_budget:
                continue
            injected.append(item)
            injected_tokens += item_tokens
        scored_memories = injected
        memories = [item.memory for item in scored_memories]
        memory_context = "\n".join(f"- [{'项目' if memory.project_id else '个人'}] {memory.content}" for memory in memories)
        await emit(
            "memory.retrieved",
            {
                "count": len(memories),
                "memories": [
                    {
                        "id": str(item.memory.id),
                        "project_id": str(item.memory.project_id) if item.memory.project_id else None,
                        "content": item.memory.content,
                        "kind": item.memory.kind.value,
                        "score": round(item.score, 4),
                        "keyword_score": round(item.keyword_score, 4),
                        "vector_score": round(item.vector_score, 4),
                        "recency_score": round(item.recency_score, 4),
                        "importance_score": round(item.importance_score, 4),
                        "type_score": round(item.type_score, 4),
                        "reason": item.reason,
                    }
                    for item in scored_memories
                ],
            },
        )
        try:
            chat_model = self.chat_model or create_chat_model(
                settings=self.settings, config=self.business
            )
            stored_messages = await repository.list_messages(
                conversation.id, limit=200
            )
            # Release the read transaction before optional model summarization.
            await self.session.commit()
            context = await prepare_conversation_context(
                self.session,
                conversation=conversation,
                messages=stored_messages,
                chat_model=chat_model,
                config=self.business.agent,
                reserved_tokens=injected_tokens + workspace_tokens,
            )
            await emit(
                "session.loaded",
                {
                    "message_count": len(context.messages),
                    "estimated_tokens": context.estimated_tokens,
                    "budget": self.business.agent.context_token_budget,
                    "reserved_tokens": context.reserved_tokens,
                    "summary_used": bool(context.summary),
                    "truncated": context.truncated,
                },
            )
            if context.summarized:
                await emit("session.summary_updated")
            tool_registry = build_request_registry(
                self.session,
                user_id=user.id,
                conversation_id=conversation.id,
                source_message_id=user_message.id,
                business=self.business,
                embedding_model=self.embedding_model,
                emit=emit,
                manager=self.mcp_manager,
                execution_directory=directory,
                project_id=project_id,
            )
            async def authorize(tool, call, execution):
                return await self.broker.authorize(tool, call, execution, lifecycle, self.business.local.approval_timeout_seconds)

            execution_context = ToolExecutionContext(
                run_id=str(run.id), agent_id="memory-agent",
                user_id=str(user.id), conversation_id=str(conversation.id),
                source_message_id=str(user_message.id), granted_permissions=request_permissions(local=self.business.local.enabled and directory is not None),
                authorize=authorize,
                project_id=str(project_id) if project_id else None,
                execution_directory=directory, execution_mode=execution_mode,
                change_journal=journal,
                audit_sink=CallbackAuditSink(emit),
            )
            executor = ToolExecutor(tool_registry)
            checkpointer = get_checkpointer()
            graph_messages = context.messages
            tool_round_limit = (self.business.agent.project_max_tool_rounds if project_id
                                else self.business.agent.max_tool_rounds)
            if checkpointer is not None:
                graph_messages = replace_checkpoint_messages(graph_messages)
            result = await build_graph(
                chat_model,
                tools=tool_registry.function_schemas(),
                tool_executor=executor,
                tool_context=execution_context,
                max_tool_rounds=tool_round_limit,
                system_prompt="\n\n".join(part for part in (SYSTEM_PROMPT, workspace_context) if part),
                checkpointer=checkpointer,
                output_sink=emit,
            ).ainvoke(
                {
                    "messages": graph_messages,
                    "current_input": redaction.text,
                    "conversation_summary": context.summary,
                    "memory_context": memory_context,
                    "retrieved_memories": [],
                    "tool_rounds": 0,
                    "tool_limit_reached": False,
                },
                config={
                    "configurable": {"thread_id": str(conversation.id)},
                    "recursion_limit": tool_round_limit * 2 + 10,
                },
            )
            if result.get("tool_limit_reached"):
                await emit("agent.tool_limit_reached", {"tool_rounds": result["tool_rounds"], "limit": tool_round_limit})
            answer = result["messages"][-1].text
            await emit("model.completed", {"answer_length": len(answer)})
            return await finish_success(
                answer, memory_count=len(memories), queue_extraction=project_id is None
            )
        except Exception as error:
            await finish_failure(error)
            raise
