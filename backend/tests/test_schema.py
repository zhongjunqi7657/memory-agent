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

    messages = Base.metadata.tables["messages"]
    assert any(
        constraint.name == "uq_messages_conversation_sequence"
        for constraint in messages.constraints
    )
