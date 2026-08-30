"""Hybrid keyword/vector ranking for a small user-scoped memory set."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.persistence.models import Memory


@dataclass(frozen=True)
class ScoredMemory:
    memory: Memory
    score: float
    keyword_score: float
    vector_score: float
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
    return max(0.0, min(1.0, sum(a * b for a, b in zip(left, right)) / left_norm / right_norm))


def rank_memories(
    memories: list[Memory],
    query: str,
    *,
    query_embedding: list[float] | None = None,
    vector_weight: float = 0.7,
    keyword_weight: float = 0.3,
    limit: int = 5,
) -> list[ScoredMemory]:
    """Rank active memories and explain whether keyword or vector evidence matched."""

    has_vectors = query_embedding is not None and any(memory.embedding for memory in memories)
    vector_weight = vector_weight if has_vectors else 0.0
    keyword_weight = keyword_weight if keyword_weight > 0 else 1.0
    total_weight = vector_weight + keyword_weight
    ranked: list[ScoredMemory] = []
    for memory in memories:
        keyword_score = keyword_similarity(query, memory.content)
        vector_score = cosine_similarity(memory.embedding, query_embedding) if has_vectors else 0.0
        score = (vector_weight * vector_score + keyword_weight * keyword_score) / total_weight
        if query.strip() and score <= 0:
            continue
        evidence = []
        if keyword_score > 0:
            evidence.append("关键词")
        if vector_score > 0:
            evidence.append("语义向量")
        ranked.append(
            ScoredMemory(
                memory=memory,
                score=score,
                keyword_score=keyword_score,
                vector_score=vector_score,
                reason=" + ".join(evidence) if evidence else "时间排序兜底",
            )
        )
    ranked.sort(key=lambda item: (item.score, item.memory.updated_at), reverse=True)
    return ranked[:limit]
