from datetime import datetime, timezone
from types import SimpleNamespace

from app.memory.retrieval import cosine_similarity, keyword_similarity, rank_memories


def memory(content: str, embedding: list[float] | None = None):
    return SimpleNamespace(content=content, embedding=embedding, updated_at=datetime.now(timezone.utc))


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
    assert ranked[0].reason == "关键词"


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
