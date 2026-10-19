"""Exclude a second API runtime before it can interrupt this instance's runs."""

from contextlib import asynccontextmanager

from sqlalchemy import text

RUNTIME_LOCK_KEY = 0x4D454D4147454E54  # Fixed application protocol identifier.


@asynccontextmanager
async def runtime_lease(engine=None):
    if engine is None:
        from app.persistence.db import engine

    # Session advisory locks need a dedicated physical connection. Autocommit
    # avoids keeping a database transaction open for the application's lifetime.
    async with engine.connect() as connection:
        connection = await connection.execution_options(isolation_level="AUTOCOMMIT")
        locked = await connection.scalar(text("SELECT pg_try_advisory_lock(:key)"), {"key": RUNTIME_LOCK_KEY})
        if not locked:
            raise RuntimeError("该数据库已有运行中的应用实例；请使用单进程、单实例启动")
        try:
            yield
        finally:
            # Do not return a locked connection to the pool if unlocking fails.
            try:
                await connection.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": RUNTIME_LOCK_KEY})
            except BaseException:
                await connection.invalidate()
                raise
