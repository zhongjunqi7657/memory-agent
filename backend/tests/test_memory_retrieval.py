from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.memory.retrieval import cosine_similarity, keyword_similarity, rank_memories
from app.persistence.models import MemoryKind
from app.persistence.repositories import MemoryRepository


def memory(
    content: str,
    embedding: list[float] | None = None,
    *,
    kind: str = "semantic",
    importance: float = 0.5,
    updated_at: datetime | None = None,
):
    return SimpleNamespace(
        content=content,
        embedding=embedding,
        kind=kind,
        importance=importance,
        updated_at=updated_at or datetime.now(timezone.utc),
    )


def test_keyword_similarity_prefers_exact_phrase() -> None:
    assert keyword_similarity("学习 LangGraph", "我正在学习 LangGraph") == 1.0
    assert keyword_similarity("数据库", "我在准备前端面试") == 0.0


def test_vector_similarity_is_safe_for_missing_or_mismatched_vectors() -> None:
    assert cosine_similarity(None, [1.0]) == 0.0
    assert cosine_similarity([1.0], [1.0, 2.0]) == 0.0
    assert cosine_similarity([1.0, 0.0], [0.5, 0.5]) > 0.7


def test_hybrid_ranking_falls_back_to_keywords_without_vectors() -> None:
    ranked = rank_memories(
        [memory("我准备学习 FastAPI"), memory("我喜欢摄影")],
        "FastAPI",
        query_embedding=[1.0, 0.0],
        limit=2,
    )
    assert len(ranked) == 1
    assert ranked[0].memory.content == "我准备学习 FastAPI"
    assert "关键词" in ranked[0].reason


def test_hybrid_ranking_can_use_semantic_signal() -> None:
    ranked = rank_memories(
        [memory("我在学习后端工程", [1.0, 0.0]), memory("我喜欢摄影", [0.9, 0.1])],
        "数据库工程",
        query_embedding=[1.0, 0.0],
        vector_weight=0.8,
        keyword_weight=0.2,
        limit=1,
    )
    assert ranked[0].memory.content == "我在学习后端工程"
    assert "语义向量" in ranked[0].reason


def test_complete_ranking_uses_recency_importance_and_memory_type() -> None:
    now = datetime(2026, 9, 7, tzinfo=timezone.utc)
    recent_important_episode = memory(
        "用户这周完成了 LangGraph 工具练习",
        [1.0, 0.0],
        kind="episodic",
        importance=0.9,
        updated_at=now - timedelta(days=1),
    )
    old_semantic_memory = memory(
        "用户长期学习 LangGraph",
        [1.0, 0.0],
        kind="semantic",
        importance=0.2,
        updated_at=now - timedelta(days=180),
    )

    ranked = rank_memories(
        [old_semantic_memory, recent_important_episode],
        "这周 LangGraph 的进展",
        query_embedding=[1.0, 0.0],
        now=now,
        limit=2,
    )

    assert ranked[0].memory is recent_important_episode
    assert ranked[0].recency_score > ranked[1].recency_score
    assert ranked[0].importance_score == 0.9
    assert ranked[0].type_score == 1.0


class _ScalarResult:
    def __init__(self, items):
        self.items = items

    def all(self):
        return self.items


class _PostgresSession:
    def __init__(self, candidate):
        self.candidate = candidate
        self.statements = []

    def get_bind(self):
        return SimpleNamespace(dialect=SimpleNamespace(name="postgresql"))

    async def scalars(self, statement):
        self.statements.append(statement)
        return _ScalarResult([self.candidate])


@pytest.mark.asyncio
async def test_postgres_candidate_query_uses_pgvector_distance_ordering() -> None:
    candidate = SimpleNamespace(
        id=uuid4(),
        content="用户正在学习 LangGraph",
        embedding=[1.0, 0.0],
        updated_at=datetime.now(timezone.utc),
        importance=0.8,
        kind=MemoryKind.SEMANTIC,
    )
    session = _PostgresSession(candidate)

    matches = await MemoryRepository(session).search_hybrid(
        uuid4(), "LangGraph", query_embedding=[1.0, 0.0], limit=5
    )

    assert matches[0].memory is candidate
    assert len(session.statements) == 2
    assert "<=>" in str(session.statements[1])
