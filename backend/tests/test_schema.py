from app.api.memories import MemoryResponse
from app.persistence import Base


def test_initial_schema_contract() -> None:
    assert set(Base.metadata.tables) == {
        "users",
        "conversations",
        "messages",
        "runs",
        "run_events",
        "memories",
        "extraction_jobs",
    }

    memories = Base.metadata.tables["memories"]
    assert memories.c.embedding.type.dim == 1024
    assert memories.c.user_id.nullable is False
    assert memories.c.content.nullable is False
    assert "ix_memories_embedding_hnsw" in {index.name for index in memories.indexes}

    messages = Base.metadata.tables["messages"]
    assert any(
        constraint.name == "uq_messages_conversation_sequence"
        for constraint in messages.constraints
    )

    jobs = Base.metadata.tables["extraction_jobs"]
    assert jobs.c.available_at.nullable is False
    assert jobs.c.idempotency_key.nullable is False
    assert {"locked_at", "locked_by", "last_error"}.issubset(
        set(jobs.c.keys())
    )
    assert any(
        constraint.name == "uq_extraction_jobs_idempotency_key"
        for constraint in jobs.constraints
    )


def test_memory_response_reads_the_reserved_metadata_column() -> None:
    response = MemoryResponse.model_validate(
        {
            "id": "b9b4d4c2-125c-4a3f-a9e6-0aa3b9e8df53",
            "kind": "semantic",
            "status": "active",
            "sensitivity": "normal",
            "content": "喜欢先理解原理再看代码",
            "confidence": "0.95",
            "canonical_key": None,
            "metadata_": {"reason": "明确表达"},
            "source_message_id": None,
        }
    )
    assert response.metadata["reason"] == "明确表达"
