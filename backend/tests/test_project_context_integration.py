"""Selected project identity must reach the model, including disabled-tool sessions."""

import json
from uuid import uuid4

import pytest
from langchain_core.language_models.fake_chat_models import FakeChatModel
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from sqlalchemy import select

from app.agent.service import AgentService
from app.config.business import AgentConfig, BusinessConfig, ToolConfig
from app.config.local import LocalExecutionConfig
from app.config.settings import Settings
from app.execution.controller import RunController
from app.persistence.repositories import ConversationRepository
from app.persistence.workspaces import TaskSnapshot
from app.workspace.service import bind_project
from tests.test_run_control_integration import remove_users


class ProjectAuditModel(FakeChatModel):
    project_name: str = ""
    project_id: str = ""
    directory: str = ""
    local_available: bool = False
    available_tools: tuple[str, ...] = ()

    def bind_tools(self, tools, **kwargs):
        self.available_tools = tuple(tool["function"]["name"] for tool in tools)
        return self

    async def _agenerate(self, messages, **kwargs):
        system = str(messages[0].content)
        if self.project_id:
            assert self.project_name in system
            assert self.project_id in system
            # JSON encoding preserves newlines/quotes as data inside project metadata.
            assert json.dumps(self.directory, ensure_ascii=False)[1:-1] in system
            if not self.local_available:
                assert "未启用" in system
        else:
            assert "selected_project" not in system
            assert "project-context-" not in system
        if self.local_available:
            assert "builtin__local__read" in self.available_tools
            results = [message for message in messages if isinstance(message, ToolMessage)]
            if not results:
                answer = AIMessage(content="", tool_calls=[{
                    "id": "read-readme", "name": "builtin__local__read", "args": {"path": "README.md"},
                }])
                return ChatResult(generations=[ChatGeneration(message=answer)])
            assert "Project context marker" in str(results[-1].content)
        else:
            assert not any(name.startswith("builtin__local__") for name in self.available_tools)
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content="project verified"))])


@pytest.mark.asyncio
@pytest.mark.parametrize("local_enabled,tools_enabled", [(True, True), (False, True), (True, False)])
async def test_selected_project_reaches_model_and_reads_only_when_tools_enabled(
    postgres_factory, tmp_path, local_enabled, tools_enabled,
):
    key, controller = f"project-context-{uuid4()}", RunController()
    (tmp_path / "README.md").write_text("# Project context marker\n", encoding="utf-8")
    business = BusinessConfig(local=LocalExecutionConfig(enabled=local_enabled), tools=ToolConfig(enabled=tools_enabled))
    try:
        async with postgres_factory() as session:
            repository = ConversationRepository(session)
            user = await repository.get_or_create_user(key)
            project = await bind_project(session, user.id, str(tmp_path), key)
            await session.commit()
            project_id, directory = project.id, project.directory

        async def run(*, project_id=None, conversation_id=None):
            model = ProjectAuditModel(project_name=key if project_id or conversation_id else "",
                                      project_id=str(project.id) if project_id or conversation_id else "",
                                      directory=directory,
                                      local_available=bool((project_id or conversation_id) and local_enabled and tools_enabled))
            async with postgres_factory() as session:
                return await AgentService(session, chat_model=model, embedding_model=None, business=business,
                                          settings=Settings(_env_file=None, dashscope_api_key=""),
                                          controller=controller).chat(
                    user_key=key, content="审计本项目", project_id=project_id, conversation_id=conversation_id,
                )

        response = await run(project_id=project_id)
        assert response["message"] == "project verified"
        # Reopening/continuing without resending a project ID must restore its identity.
        await run(conversation_id=response["conversation_id"])
        # A new ordinary conversation must not inherit the previous selected project.
        await run()
        async with postgres_factory() as session:
            snapshot = await session.scalar(select(TaskSnapshot).where(TaskSnapshot.run_id == response["run_id"]))
            assert bool(snapshot) is (local_enabled and tools_enabled)
    finally:
        await controller.close()
        await remove_users(postgres_factory, key)


@pytest.mark.asyncio
@pytest.mark.parametrize("project_limit", [None, 3])
async def test_project_audit_can_read_more_than_four_modules_or_report_partial_findings(postgres_factory, tmp_path, project_limit):
    class MultiFileAuditModel(FakeChatModel):
        def bind_tools(self, tools, **kwargs):
            return self

        async def _agenerate(self, messages, **kwargs):
            reads = [message for message in messages if isinstance(message, ToolMessage) and message.status == "success"]
            if messages[-1].type == "system":
                assert len(reads) == 3
                return ChatResult(generations=[ChatGeneration(message=AIMessage(content="已检查 3 个模块，仍有 3 个模块未检查。"))])
            if len(reads) < 6:
                return ChatResult(generations=[ChatGeneration(message=AIMessage(content="", tool_calls=[{
                    "id": str(len(reads)), "name": "builtin__local__read", "args": {"path": f"module{len(reads)}.py"},
                }]))])
            assert all(f"module {index} evidence" in str(reads[index].content) for index in range(6))
            return ChatResult(generations=[ChatGeneration(message=AIMessage(content="已检查全部 6 个模块。"))])

    key, controller = f"audit-rounds-{uuid4()}", RunController()
    for index in range(6):
        (tmp_path / f"module{index}.py").write_text(f"# module {index} evidence\n", encoding="utf-8")
    try:
        async with postgres_factory() as session:
            user = await ConversationRepository(session).get_or_create_user(key)
            project = await bind_project(session, user.id, str(tmp_path), key)
            await session.commit()
            config = AgentConfig() if project_limit is None else AgentConfig(project_max_tool_rounds=project_limit)
            response = await AgentService(session, chat_model=MultiFileAuditModel(), embedding_model=None,
                                          business=BusinessConfig(agent=config, local=LocalExecutionConfig(enabled=True)),
                                          settings=Settings(_env_file=None, dashscope_api_key=""), controller=controller).chat(
                user_key=key, project_id=project.id, content="审计本项目",
            )
        assert ("全部 6 个模块" if project_limit is None else "3 个模块未检查") in response["message"]
    finally:
        await controller.close()
        await remove_users(postgres_factory, key)
