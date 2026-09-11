from types import SimpleNamespace

import pytest

from app.config.business import ModelConfig
from app.memory.embeddings import (
    embed_text,
    embedding_is_current,
    embedding_job_matches,
    mark_embedding_ready,
)


class FailingEmbeddingModel:
    async def aembed_query(self, _text: str):
        raise RuntimeError("temporary provider error")


class WrongDimensionEmbeddingModel:
    async def aembed_query(self, _text: str):
        return [1.0]


@pytest.mark.asyncio
async def test_embedding_attempt_explains_provider_and_dimension_failures() -> None:
    unavailable = await embed_text(None, "查询", expected_dimensions=2)
    failed = await embed_text(
        FailingEmbeddingModel(), "查询", expected_dimensions=2
    )
    mismatched = await embed_text(
        WrongDimensionEmbeddingModel(), "查询", expected_dimensions=2
    )

    assert unavailable.reason == "provider_unavailable"
    assert failed.reason == "provider_error"
    assert mismatched.reason == "dimension_mismatch"
    assert mismatched.actual_dimensions == 1


def test_embedding_metadata_detects_content_and_model_drift() -> None:
    config = ModelConfig(embedding_model="embedding-a", embedding_dimensions=2)
    memory = SimpleNamespace(
        content="用户偏好先理解原理",
        embedding=[1.0, 0.0],
        metadata_=mark_embedding_ready(
            {},
            content="用户偏好先理解原理",
            model=config.embedding_model,
            dimensions=config.embedding_dimensions,
        ),
    )

    assert embedding_is_current(memory, config=config) is True

    memory.content = "用户偏好直接看示例"
    assert embedding_is_current(memory, config=config) is False

    memory.content = "用户偏好先理解原理"
    changed_model = ModelConfig(
        embedding_model="embedding-b", embedding_dimensions=2
    )
    assert embedding_is_current(memory, config=changed_model) is False


def test_embedding_job_rejects_stale_content_or_profile() -> None:
    config = ModelConfig(embedding_model="embedding-a", embedding_dimensions=2)
    memory = SimpleNamespace(content="用户正在学习 LangGraph")
    payload = {
        "content_version": "stale",
        "embedding_model": config.embedding_model,
        "embedding_dimensions": config.embedding_dimensions,
    }

    assert embedding_job_matches(payload, memory, config=config) is False

    payload["content_version"] = ""
    assert embedding_job_matches(payload, memory, config=config) is False

