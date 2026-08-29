"""Database-backed extraction worker; run with ``python -m app.jobs.worker``."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.memory.extractor import MemoryExtractor
from app.persistence.db import SessionFactory
from app.persistence.models import ExtractionJob, ExtractionJobStatus


async def claim_next_job():
    async with SessionFactory() as session, session.begin():
        job = await session.scalar(
            select(ExtractionJob)
            .where(ExtractionJob.status == ExtractionJobStatus.QUEUED)
            .order_by(ExtractionJob.created_at)
            .options(selectinload(ExtractionJob.message))
            .with_for_update(skip_locked=True)
        )
        if job is None:
            return None
        job.status = ExtractionJobStatus.RUNNING
        job.attempts += 1
        job.started_at = datetime.now(timezone.utc)
        return job.id


async def process_one() -> bool:
    job_id = await claim_next_job()
    if job_id is None:
        return False
    async with SessionFactory() as session:
        job = await session.scalar(
            select(ExtractionJob)
            .where(ExtractionJob.id == job_id)
            .options(selectinload(ExtractionJob.message))
        )
        if job is None:
            return False
        try:
            await MemoryExtractor(session).extract_and_store(
                user_id=job.user_id, message=job.message
            )
            job.status = ExtractionJobStatus.COMPLETED
            job.finished_at = datetime.now(timezone.utc)
        except Exception as error:  # noqa: BLE001 - mark the durable job as failed
            job.status = ExtractionJobStatus.FAILED
            job.error_message = str(error)[:500]
            job.finished_at = datetime.now(timezone.utc)
        await session.commit()
    return True


async def worker_loop(poll_seconds: float = 2.0) -> None:
    while True:
        processed = await process_one()
        if not processed:
            await asyncio.sleep(poll_seconds)


if __name__ == "__main__":
    asyncio.run(worker_loop())
