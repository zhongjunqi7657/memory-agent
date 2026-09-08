"""Explainable ranking for user-scoped long-term memories."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.persistence.models import Memory


@dataclass(frozen=True)
class ScoredMemory:
    memory: Memory
    score: float
    keyword_score: float
    vector_score: float
    recency_score: float
    importance_score: float
    type_score: float
    reason: str


def tokenize(text: str) -> set[str]:
    """Keep CJK characters and latin/numeric words useful for simple overlap."""

    return set(re.findall(r"[\u4e00-\u9fff]|[a-zA-Z0-9_]+", text.casefold()))


def keyword_similarity(query: str, content: str) -> float:
    query_tokens = tokenize(query)
    content_tokens = tokenize(content)
    if not query_tokens or not content_tokens:
        return 0.0
    if query.strip() and query.casefold() in content.casefold():
        return 1.0
    return len(query_tokens & content_tokens) / len(query_tokens | content_tokens)


def cosine_similarity(left: list[float] | None, right: list[float] | None) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if left_norm == 0 or right_norm == 0:
        return 0.0
    similarity = sum(a * b for a, b in zip(left, right)) / left_norm / right_norm
    return max(0.0, min(1.0, similarity))


def recency_score(
    updated_at: datetime | None, *, now: datetime, half_life_days: int
) -> float:
    if updated_at is None:
        return 0.0
    if updated_at.tzinfo is None:
        updated_at = updated_at.replace(tzinfo=timezone.utc)
    age_days = max(0.0, (now - updated_at).total_seconds() / 86400)
    return math.pow(0.5, age_days / half_life_days)


def type_match_score(query: str, kind: object) -> float:
    kind_value = getattr(kind, "value", kind)
    temporal = re.search(
        r"(?:最近|这周|本周|上周|之前|经历|进展|什么时候|时间线)", query
    )
    stable = re.search(r"(?:喜欢|偏好|习惯|目标|学习方式|长期|原则)", query)
    if temporal:
        return 1.0 if kind_value == "episodic" else 0.25
    if stable:
        return 1.0 if kind_value == "semantic" else 0.25
    return 0.5


def rank_memories(
    memories: list[Memory],
    query: str,
    *,
    query_embedding: list[float] | None = None,
    vector_weight: float = 0.55,
    keyword_weight: float = 0.20,
    recency_weight: float = 0.10,
    importance_weight: float = 0.10,
    type_weight: float = 0.05,
    recency_half_life_days: int = 30,
    min_relevance_score: float = 0.08,
    now: datetime | None = None,
    limit: int = 5,
) -> list[ScoredMemory]:
    """Rank candidates by relevance, freshness, importance and memory type."""

    now = now or datetime.now(timezone.utc)
    has_vectors = query_embedding is not None and any(
        memory.embedding for memory in memories
    )
    vector_weight = vector_weight if has_vectors else 0.0
    total_weight = sum(
        (
            vector_weight,
            keyword_weight,
            recency_weight,
            importance_weight,
            type_weight,
        )
    )
    if total_weight <= 0:
        return []
    ranked: list[ScoredMemory] = []
    for memory in memories:
        keyword_score = keyword_similarity(query, memory.content)
        vector_score = (
            cosine_similarity(memory.embedding, query_embedding) if has_vectors else 0.0
        )
        memory_recency_score = recency_score(
            getattr(memory, "updated_at", None),
            now=now,
            half_life_days=recency_half_life_days,
        )
        memory_importance_score = max(
            0.0, min(1.0, float(getattr(memory, "importance", 0.5)))
        )
        memory_type_score = type_match_score(query, getattr(memory, "kind", None))
        score = (
            vector_weight * vector_score
            + keyword_weight * keyword_score
            + recency_weight * memory_recency_score
            + importance_weight * memory_importance_score
            + type_weight * memory_type_score
        ) / total_weight
        relevant = keyword_score > 0 or vector_score >= min_relevance_score
        if query.strip() and not relevant:
            continue
        evidence = []
        if keyword_score > 0:
            evidence.append("关键词")
        if vector_score > 0:
            evidence.append("语义向量")
        if memory_recency_score >= 0.5:
            evidence.append("时间")
        if memory_importance_score >= 0.7:
            evidence.append("重要度")
        if memory_type_score == 1:
            evidence.append("类型")
        ranked.append(
            ScoredMemory(
                memory=memory,
                score=score,
                keyword_score=keyword_score,
                vector_score=vector_score,
                recency_score=memory_recency_score,
                importance_score=memory_importance_score,
                type_score=memory_type_score,
                reason=" + ".join(evidence),
            )
        )
    ranked.sort(key=lambda item: (item.score, item.memory.updated_at), reverse=True)
    return ranked[:limit]
