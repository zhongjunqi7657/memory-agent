"""Adversarial stage 5 scenarios using the isolated PostgreSQL test database."""

import json

import pytest

from app.execution.controller import RunController
from app.memory.policy import MemoryCandidate
from app.memory.service import MemoryService
from app.persistence.models import Memory, MemoryKind, MemoryStatus, MessageRole
from app.persistence.repositories import ConversationRepository, MemoryRepository
from tests.test_memory_scope_flows import chat
from tests.test_memory_scopes import add
from tests.test_memory_scopes import scopes as scopes  # noqa: PLC0414
from tests.test_run_control_integration import api


@pytest.mark.parametrize("operation", ["list", "timeline", "review", "edit", "undo"])
async def test_foreign_user_cannot_read_or_mutate_project_memories(scopes, operation):
    factory, key, user, (project, _) = scopes
    foreign_key = f"{key}-foreign"
    async with factory() as session:
        await ConversationRepository(session).get_or_create_user(foreign_key)
        memory = await add(session, user, project.id, "项目约定使用 pytest")
        await MemoryService(session, project_id=project.id).update_memory(memory, importance=0.9)
        await session.commit()
        memory_id, before = memory.id, dict(memory.metadata_)
    params = {"user_key": foreign_key, "project_id": str(project.id)}
    async with api(factory, RunController()) as client:
        if operation in {"list", "timeline"}:
            path = "memories" if operation == "list" else "timeline"
            response = await client.get(f"/v1/{path}", params=params)
        elif operation == "review":
            response = await client.post("/v1/reviews", json=params)
        elif operation == "edit":
            response = await client.patch(f"/v1/memories/{memory_id}", params=params,
                                          json={"status": "deleted"})
        else:
            response = await client.post(f"/v1/memories/{memory_id}/undo", params=params)
        assert response.status_code == 404, response.text
        assert "项目约定使用 pytest" not in response.text
    async with factory() as session:
        persisted = await session.get(Memory, memory_id)
        assert persisted.status == MemoryStatus.ACTIVE
        assert float(persisted.importance) == 0.9
        assert persisted.metadata_ == before


@pytest.mark.parametrize("direction", ["personal_to_project", "project_to_personal"])
@pytest.mark.parametrize("operation", ["edit", "undo"])
async def test_mutation_requires_exact_scope_even_with_include_personal(scopes, direction, operation):
    factory, key, user, (project, _) = scopes
    project_id = None if direction == "personal_to_project" else project.id
    async with factory() as session:
        memory = await add(session, user, project_id, "约定使用中文说明")
        await MemoryService(session, project_id=project_id).update_memory(memory, importance=0.9)
        await session.commit()
        memory_id = memory.id
    params = {"user_key": key, "include_personal": "true"}
    if direction == "personal_to_project":
        params["project_id"] = str(project.id)
    async with api(factory, RunController()) as client:
        if operation == "edit":
            response = await client.patch(f"/v1/memories/{memory_id}", params=params,
                                          json={"status": "deleted"})
        else:
            response = await client.post(f"/v1/memories/{memory_id}/undo", params=params)
        assert response.status_code == 404
    async with factory() as session:
        persisted = await session.get(Memory, memory_id)
        assert persisted.status == MemoryStatus.ACTIVE
        assert float(persisted.importance) == 0.9


async def test_read_union_status_filter_and_personal_default(scopes):
    factory, key, user, (a, b) = scopes
    async with factory() as session:
        personal = await add(session, user, None, "用户偏好中文说明")
        active = await add(session, user, a.id, "项目采用 pytest")
        pending = await add(session, user, a.id, "项目采用 unittest", canonical_key="test_runner")
        await MemoryService(session, project_id=a.id).update_memory(pending, status=MemoryStatus.PENDING)
        await add(session, user, b.id, "其他项目采用 vitest")
        await session.commit()
    async with api(factory, RunController()) as client:
        params = {"user_key": key, "include_personal": "true", "project_id": str(a.id)}
        response = await client.get("/v1/memories", params=params)
        assert response.status_code == 200
        assert {item["id"] for item in response.json()} == {str(m.id) for m in (personal, active, pending)}
        params["status"] = "active"
        response = await client.get("/v1/memories", params=params)
        assert {item["id"] for item in response.json()} == {str(personal.id), str(active.id)}
        response = await client.get("/v1/timeline", params={"user_key": key, "project_id": str(a.id)})
        assert {item["id"] for item in response.json()} == {str(active.id)}
        response = await client.get("/v1/memories", params={"user_key": key, "include_personal": "true"})
        assert {item["id"] for item in response.json()} == {str(personal.id)}


async def test_duplicate_evidence_merges_only_within_project_and_retry_is_idempotent(scopes):
    factory, _, user, (a, b) = scopes
    async with factory() as session:
        repository = ConversationRepository(session)
        conversation = await repository.create_conversation(user.id)
        conversation.project_id = a.id
        messages = [await repository.add_message(conversation.id, role=MessageRole.USER,
                    content="记住项目采用 pytest", sequence=index) for index in (1, 2)]
        content = "项目采用 pytest"
        personal = await add(session, user, None, content)
        other = await add(session, user, b.id, content)
        service = MemoryService(session, project_id=a.id)
        candidate = MemoryCandidate(content=content, kind=MemoryKind.SEMANTIC, confidence=1, explicit=True)
        first = await service.add_candidate(user_id=user.id, candidate=candidate,
                    source_message_id=messages[0].id, conversation_id=conversation.id)
        for _ in range(2):
            merged = await service.add_candidate(user_id=user.id, candidate=candidate,
                        source_message_id=messages[1].id, conversation_id=conversation.id)
            assert merged.id == first.id
        assert first.source_message_ids == [str(message.id) for message in messages]
        assert first.metadata_["evidence_count"] == 2
        assert personal.source_message_ids == other.source_message_ids == []
        assert len(await MemoryRepository(session, project_id=a.id).list_for_user(user.id)) == 1


@pytest.mark.parametrize("source_scope", ["personal", "other_project", "foreign_user"])
async def test_project_write_rejects_conversation_from_wrong_scope(scopes, source_scope):
    factory, key, user, (a, b) = scopes
    async with factory() as session:
        repository = ConversationRepository(session)
        owner = (await repository.get_or_create_user(f"{key}-foreign")) if source_scope == "foreign_user" else user
        conversation = await repository.create_conversation(owner.id)
        conversation.project_id = b.id if source_scope == "other_project" else None
        with pytest.raises(ValueError, match="来源会话"):
            await MemoryService(session, project_id=a.id).add_explicit(user_id=user.id,
                content="项目采用 pytest", source_message_id=None, conversation_id=conversation.id)
        assert not await MemoryRepository(session, project_id=a.id).list_for_user(user.id)


async def test_correction_confirmation_and_undo_preserve_other_scopes(scopes):
    factory, _, user, (a, b) = scopes
    async with factory() as session:
        old = "项目测试约定使用 pytest"
        personal = await add(session, user, None, old)
        first = await add(session, user, a.id, old)
        other = await add(session, user, b.id, old)
        service = MemoryService(session, project_id=a.id)
        replacement, superseded = await service.correct(user_id=user.id, old_query=old,
                    replacement="项目测试约定使用 unittest", source_message_id=None)
        assert replacement.status == MemoryStatus.PENDING
        assert replacement.metadata_["conflicts_with"] == [str(first.id)]
        assert superseded == []
        assert first.status == personal.status == other.status == MemoryStatus.ACTIVE
        await service.update_memory(replacement, status=MemoryStatus.ACTIVE)
        assert first.status == MemoryStatus.SUPERSEDED
        assert personal.status == other.status == MemoryStatus.ACTIVE
        await service.undo(replacement)
        assert replacement.status == MemoryStatus.PENDING
        assert first.status == personal.status == other.status == MemoryStatus.ACTIVE


@pytest.mark.parametrize("command", ["查看我的记忆", "忘记项目测试使用 pytest", "把项目测试使用 pytest改成项目测试使用 unittest"])
async def test_project_chat_commands_do_not_operate_on_personal_or_other_project(scopes, command):
    factory, key, user, (a, b) = scopes
    async with factory() as session:
        personal = await add(session, user, None, "个人独有标记：项目测试使用 pytest")
        first = await add(session, user, a.id, "项目测试使用 pytest")
        other = await add(session, user, b.id, "其他项目独有标记：项目测试使用 pytest")
        await session.commit()
    result, _ = await chat(factory, key, a.id, content=command)
    assert "个人独有标记" not in result["message"]
    assert "其他项目独有标记" not in result["message"]
    async with factory() as session:
        assert (await session.get(Memory, personal.id)).status == MemoryStatus.ACTIVE
        assert (await session.get(Memory, other.id)).status == MemoryStatus.ACTIVE
        current = await session.get(Memory, first.id)
        assert current.status == (MemoryStatus.DELETED if command.startswith("忘记") else MemoryStatus.ACTIVE)
        if command.startswith("把"):
            pending = await MemoryRepository(session, project_id=a.id).list_for_user(user.id, status=MemoryStatus.PENDING)
            assert len(pending) == 1
            assert pending[0].content == "项目测试使用 unittest"
            assert pending[0].metadata_["conflicts_with"] == [str(first.id)]


@pytest.mark.parametrize("source", ["explicit", "different_target", "redacted", "assistant", "other_conversation"])
async def test_legacy_project_proposal_requires_matching_unredacted_user_source(scopes, source):
    factory, key, user, (a, _) = scopes
    async with factory() as session:
        repository = ConversationRepository(session)
        conversation = await repository.create_conversation(user.id)
        conversation.project_id = a.id
        source_conversation = conversation
        if source == "other_conversation":
            source_conversation = await repository.create_conversation(user.id)
            source_conversation.project_id = a.id
        message = await repository.add_message(source_conversation.id, sequence=1,
            role=MessageRole.ASSISTANT if source == "assistant" else MessageRole.USER,
            content="记住项目测试使用 pytest")
        message.is_redacted = source == "redacted"
        await session.commit()
    async with api(factory, RunController()) as client:
        response = await client.post("/v1/mcp/call", json={"user_key": key,
            "conversation_id": str(conversation.id), "source_message_id": str(message.id),
            "tool": "propose_memory_update", "arguments": {
                "content": "项目测试使用 unittest" if source == "different_target" else "项目测试使用 pytest"}})
        if source in {"assistant", "other_conversation"}:
            assert response.status_code == 400
        else:
            assert response.status_code == 200, response.text
            assert json.loads(response.json()["content"])["created"] == (source == "explicit")
    async with factory() as session:
        memories = await MemoryRepository(session, project_id=a.id).list_for_user(user.id)
        assert len(memories) == (1 if source == "explicit" else 0)
        if memories:
            assert memories[0].status == MemoryStatus.PENDING
            assert memories[0].source_message_id == message.id
            assert memories[0].conversation_id == conversation.id
        assert not await MemoryRepository(session).list_for_user(user.id)


async def test_rejected_secret_edit_preserves_content_embedding_and_undo(scopes):
    factory, key, user, (a, _) = scopes
    async with factory() as session:
        memory = await add(session, user, a.id, "项目测试使用 pytest")
        memory.embedding = [1.0] + [0.0] * 1023
        await MemoryService(session, project_id=a.id).update_memory(memory, importance=0.9)
        before = dict(memory.metadata_)
        await session.commit()
    async with api(factory, RunController()) as client:
        response = await client.patch(f"/v1/memories/{memory.id}", params={"user_key": key, "project_id": str(a.id)},
            json={"content": "api_key=sk-test-secret-1234567890", "status": "deleted"})
        assert response.status_code == 400
        assert "sk-test-secret" not in response.text
    async with factory() as session:
        persisted = await session.get(Memory, memory.id)
        assert persisted.content == "项目测试使用 pytest"
        assert persisted.status == MemoryStatus.ACTIVE
        assert persisted.embedding[0] == 1.0
        assert persisted.metadata_ == before
