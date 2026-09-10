from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.config.business import BusinessConfig, ModelConfig
from app.memory.conflicts import ConflictCandidateSelector, rank_conflict_candidates
from app.persistence.models import MemoryKind, MemoryStatus


class ScalarResult(list):
    def all(self):
        return list(self)


class FakeSession:
    def __init__(self, memories):
        self.memories = memories

    async def scalars(self, _statement):
        return ScalarResult(self.memories)

    def get_bind(self):
        return SimpleNamespace(dialect=SimpleNamespace(name="sqlite"))


class FakeEmbeddingModel:
    async def aembed_query(self, _query):
        return [1.0, 0.0]


def memory(
    content: str,
    embedding: list[float],
    *,
    user_id=None,
    status=MemoryStatus.ACTIVE,
):
    return SimpleNamespace(
        id=uuid4(),
        user_id=user_id or uuid4(),
        content=content,
        embedding=embedding,
        kind=MemoryKind.SEMANTIC,
        status=status,
        importance=0.8,
        updated_at=datetime.now(timezone.utc),
    )


@pytest.mark.parametrize(
    ("query", "previous_content"),
    [
        ("我决定转向求职", "用户当前目标是准备考研"),
        ("我现在更适合夜间专注", "用户偏好早晨学习"),
        ("我已经搬到上海生活了", "用户目前住在北京"),
    ],
)
def test_semantic_conflict_candidates_cover_common_stable_fact_changes(
    query: str, previous_content: str
) -> None:
    previous = memory(previous_content, [1.0, 0.0])
    unrelated = memory("用户喜欢读科幻小说", [0.0, 1.0])

    ranked = rank_conflict_candidates(
        [unrelated, previous],
        query,
        query_embedding=[1.0, 0.0],
        limit=1,
    )

    assert [item.memory for item in ranked] == [previous]
    assert ranked[0].keyword_score == 0
    assert ranked[0].vector_score == 1


@pytest.mark.asyncio
async def test_selector_returns_only_target_users_active_memories() -> None:
    user_id = uuid4()
    active = memory("用户目前住在北京", [1.0, 0.0], user_id=user_id)
    pending = memory(
        "用户可能住在杭州",
        [1.0, 0.0],
        user_id=user_id,
        status=MemoryStatus.PENDING,
    )
    deleted = memory(
        "用户曾住在广州",
        [1.0, 0.0],
        user_id=user_id,
        status=MemoryStatus.DELETED,
    )
    foreign = memory("用户目前住在上海", [1.0, 0.0])
    selector = ConflictCandidateSelector(
        FakeSession([pending, deleted, foreign, active]),
        embedding_model=FakeEmbeddingModel(),
        business=BusinessConfig(models=ModelConfig(embedding_dimensions=2)),
    )

    ranked = await selector.select(user_id=user_id, query="我搬到上海了", limit=5)

    assert [item.memory for item in ranked] == [active]
