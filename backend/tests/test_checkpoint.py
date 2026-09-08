from app.agent.checkpoint import _psycopg_url


def test_asyncpg_url_is_converted_for_postgres_checkpointer() -> None:
    converted = _psycopg_url(
        "postgresql+asyncpg://memory_agent:secret@localhost:5432/memory_agent"
    )

    assert converted == (
        "postgresql://memory_agent:secret@localhost:5432/memory_agent"
    )
