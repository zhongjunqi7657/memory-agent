"""Application-scoped PostgreSQL checkpointer lifecycle."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from sqlalchemy.engine import make_url

from app.config.settings import Settings

_checkpointer: BaseCheckpointSaver | None = None
_checkpoint_error: str | None = "not_started"
CHECKPOINT_CONNECT_TIMEOUT_SECONDS = 3
CHECKPOINT_SETUP_TIMEOUT_SECONDS = 30


def get_checkpointer() -> BaseCheckpointSaver | None:
    return _checkpointer


def checkpoint_status() -> dict[str, object]:
    return {
        "ready": _checkpointer is not None,
        "error": _checkpoint_error,
    }


def _psycopg_url(database_url: str) -> str:
    url = make_url(database_url).set(drivername="postgresql")
    return url.render_as_string(hide_password=False)


@asynccontextmanager
async def checkpoint_lifespan(settings: Settings) -> AsyncIterator[None]:
    global _checkpointer, _checkpoint_error

    if not settings.database_url.startswith("postgresql"):
        _checkpoint_error = "unsupported_database"
        yield
        return

    manager = AsyncPostgresSaver.from_conn_string(_psycopg_url(settings.database_url))
    saver = None
    try:
        saver = await asyncio.wait_for(
            manager.__aenter__(), timeout=CHECKPOINT_CONNECT_TIMEOUT_SECONDS
        )
        await asyncio.wait_for(
            saver.setup(), timeout=CHECKPOINT_SETUP_TIMEOUT_SECONDS
        )
    except Exception:  # noqa: BLE001 - health endpoint exposes only a safe status code
        if saver is not None:
            await manager.__aexit__(None, None, None)
        _checkpoint_error = "database_unavailable"
        yield
        return

    _checkpointer = saver
    _checkpoint_error = None
    try:
        yield
    finally:
        _checkpointer = None
        _checkpoint_error = "stopped"
        await manager.__aexit__(None, None, None)
