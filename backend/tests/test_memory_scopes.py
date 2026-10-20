"""Stage 5 boundaries exercised against the migrated database."""

from uuid import uuid4

import pytest

from app.config.business import BusinessConfig
from app.memory.conflicts import ConflictCandidateSelector
from app.memory.embeddings import mark_embedding_ready
from app.memory.policy import MemoryCandidate
from app.memory.service import MemoryService
from app.persistence.models import MemoryKind, MemoryStatus
from app.persistence.repositories import ConversationRepository, MemoryRepository
from app.workspace.service import bind_project
from tests.test_run_control_integration import remove_users


@pytest.fixture
async def scopes(postgres_factory, tmp_path):
    key = f"memory-scopes-{uuid4()}"
    async with postgres_factory() as session:
        user = await ConversationRepository(session).get_or_create_user(key)
        projects = []
        for name in ("a", "b"):
            path = tmp_path / name
            path.mkdir()
            projects.append(await bind_project(session, user.id, str(path), name))
        await session.commit()
    try:
        yield postgres_factory, key, user, projects
    finally:
        await remove_users(postgres_factory, key, f"{key}-foreign")


async def add(session, user, project_id, content, **kwargs):
    return await MemoryService(session, project_id=project_id).add_candidate(
        user_id=user.id, source_message_id=None,
        candidate=MemoryCandidate(content=content, kind=MemoryKind.SEMANTIC,
                                  confidence=1, explicit=True, **kwargs),
    )


async def test_scope_retrieval_dedup_conflict_confirm_undo_and_delete(scopes):
    factory, _, user, (a, b) = scopes
    async with factory() as session:
        personal = await add(session, user, None, "用户偏好中文说明", canonical_key="tests")
        first = await add(session, user, a.id, "项目测试采用 pytest", canonical_key="tests")
        other = await add(session, user, b.id, "项目测试采用 pytest", canonical_key="tests")
        assert first.id != other.id
        profile = BusinessConfig().models
        for memory in (personal, first, other):
            memory.embedding = [1.0] + [0.0] * 1023
            memory.metadata_ = mark_embedding_ready(memory.metadata_, content=memory.content,
                                                     model=profile.embedding_model, dimensions=1024)
        await session.flush()
        ranked = await MemoryRepository(session, project_id=a.id, include_personal=True).search_hybrid(
            user.id, "项目测试", query_embedding=[1.0] + [0.0] * 1023, limit=10)
        assert {item.memory.id for item in ranked} == {personal.id, first.id}
        conflicts = await ConflictCandidateSelector(session, project_id=a.id).select(user_id=user.id, query="项目测试")
        assert [item.memory.id for item in conflicts] == [first.id]
        replacement = await add(session, user, a.id, "项目测试采用 unittest", canonical_key="tests",
                                contradicts_memory_ids=(other.id, personal.id))
        assert replacement.status == MemoryStatus.PENDING
        assert replacement.metadata_["conflicts_with"] == [str(first.id)]
        # Even stale/forged conflict metadata cannot cross a scope.
        replacement.metadata_ = {**replacement.metadata_, "conflicts_with": [str(first.id), str(other.id), str(personal.id)]}
        service = MemoryService(session, project_id=a.id)
        await service.update_memory(replacement, status=MemoryStatus.ACTIVE)
        assert first.status == MemoryStatus.SUPERSEDED
        assert other.status == personal.status == MemoryStatus.ACTIVE
        await service.undo(replacement)
        assert first.status == MemoryStatus.ACTIVE
        assert other.status == personal.status == MemoryStatus.ACTIVE
        visible = await MemoryRepository(session, project_id=a.id, include_personal=True).list_for_user(user.id)
        assert {m.id for m in visible} == {personal.id, first.id, replacement.id}
        assert [m.id for m in await MemoryRepository(session).list_for_user(user.id)] == [personal.id]
        await service.forget(user_id=user.id, query="项目测试采用 pytest")
        assert first.status == MemoryStatus.DELETED
        assert other.status == MemoryStatus.ACTIVE


async def test_project_secrets_rejected_and_foreign_project_denied(scopes):
    factory, _, user, (a, _) = scopes
    async with factory() as session:
        assert await add(session, user, a.id, "api_key=sk-test-secret-1234567890") is None
        outsider = await ConversationRepository(session).get_or_create_user(f"foreign-{uuid4()}")
        with pytest.raises(ValueError, match="项目"):
            await add(session, outsider, a.id, "项目使用 Python")
