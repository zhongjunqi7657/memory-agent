"""Embedding attempts, version metadata and compatibility checks."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass

from app.config.business import ModelConfig


@dataclass(frozen=True)
class EmbeddingAttempt:
    vector: list[float] | None
    reason: str | None = None
    actual_dimensions: int | None = None

    @property
    def ready(self) -> bool:
        return self.vector is not None


def embedding_content_version(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]


def embedding_profile_version(*, model: str, dimensions: int) -> str:
    value = f"{model}:{dimensions}"
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]


async def embed_text(
    embedding_model,
    text: str,
    *,
    expected_dimensions: int,
) -> EmbeddingAttempt:
    if embedding_model is None:
        return EmbeddingAttempt(vector=None, reason="provider_unavailable")
    try:
        vector = list(await embedding_model.aembed_query(text))
    except Exception:  # noqa: BLE001 - callers intentionally degrade safely
        return EmbeddingAttempt(vector=None, reason="provider_error")
    actual_dimensions = len(vector)
    if actual_dimensions != expected_dimensions:
        return EmbeddingAttempt(
            vector=None,
            reason="dimension_mismatch",
            actual_dimensions=actual_dimensions,
        )
    return EmbeddingAttempt(vector=vector, actual_dimensions=actual_dimensions)


def mark_embedding_ready(
    metadata: Mapping[str, object] | None,
    *,
    content: str,
    model: str,
    dimensions: int,
) -> dict[str, object]:
    updated = dict(metadata or {})
    updated.update(
        {
            "embedding_status": "ready",
            "embedding_content_version": embedding_content_version(content),
            "embedding_model": model,
            "embedding_dimensions": dimensions,
        }
    )
    return updated


def mark_embedding_pending(
    metadata: Mapping[str, object] | None,
) -> dict[str, object]:
    updated = dict(metadata or {})
    updated["embedding_status"] = "pending"
    for key in (
        "embedding_content_version",
        "embedding_model",
        "embedding_dimensions",
    ):
        updated.pop(key, None)
    return updated


def mark_embedding_unavailable(
    metadata: Mapping[str, object] | None,
) -> dict[str, object]:
    updated = mark_embedding_pending(metadata)
    updated["embedding_status"] = "unavailable"
    return updated


def embedding_is_current(
    memory, *, config: ModelConfig, allow_legacy: bool = True
) -> bool:
    vector = getattr(memory, "embedding", None)
    if vector is None:
        return False
    metadata = dict(getattr(memory, "metadata_", None) or {})
    if metadata.get("embedding_status") in {"pending", "unavailable"}:
        return False
    profile_keys = {
        "embedding_content_version",
        "embedding_model",
        "embedding_dimensions",
    }
    if not profile_keys.intersection(metadata):
        return allow_legacy
    if len(vector) != config.embedding_dimensions:
        return False
    stored_content_version = metadata.get("embedding_content_version")
    if stored_content_version and stored_content_version != embedding_content_version(
        memory.content
    ):
        return False
    stored_model = metadata.get("embedding_model")
    if stored_model and stored_model != config.embedding_model:
        return False
    stored_dimensions = metadata.get("embedding_dimensions")
    return not (
        stored_dimensions and stored_dimensions != config.embedding_dimensions
    )


def embedding_job_matches(
    payload: Mapping[str, object], memory, *, config: ModelConfig
) -> bool:
    return (
        payload.get("content_version")
        == embedding_content_version(memory.content)
        and payload.get("embedding_model") == config.embedding_model
        and payload.get("embedding_dimensions") == config.embedding_dimensions
    )


def embedding_fallback_payload(
    attempt: EmbeddingAttempt,
    *,
    expected_dimensions: int,
    source: str,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "source": source,
        "reason": attempt.reason or "unknown",
        "expected_dimensions": expected_dimensions,
    }
    if attempt.actual_dimensions is not None:
        payload["actual_dimensions"] = attempt.actual_dimensions
    return payload
