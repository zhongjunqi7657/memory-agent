"""Database-backed extraction worker; run with ``python -m app.jobs.worker``."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from sqlalchemy import and_, case, or_, select
from sqlalchemy.orm import selectinload

from app.config.business import get_business_config
from app.memory.extractor import MemoryExtractor
from app.models.qwen import create_embedding_model
from app.persistence.db import SessionFactory
from app.persistence.models import ExtractionJob, ExtractionJobStatus, Memory
from app.persistence.repositories import ConversationRepository
from app.security.redaction import redact_secrets

JOB_LEASE_SECONDS = 300


def mark_job_failure(job, error: Exception, *, now: datetime, max_retries: int) -> None:
    """Transition a claimed job to retryable queued or terminal failed state."""

    job.last_error = redact_secrets(str(error)).text[:500]
    job.locked_at = None
    job.locked_by = None
    if job.attempts < max_retries:
        job.status = ExtractionJobStatus.QUEUED
        job.available_at = now + timedelta(seconds=2 ** max(job.attempts - 1, 0))
        job.finished_at = None
    else:
        job.status = ExtractionJobStatus.FAILED
        job.finished_at = now


def _run_id_for(job: ExtractionJob) -> UUID | None:
    value = job.payload.get("run_id")
    if not value:
        return None
    try:
        return UUID(str(value))
    except (TypeError, ValueError):
        return None


def _memory_change(memory: Memory, message_id: UUID) -> dict[str, object]:
    conflicts = list(memory.metadata_.get("conflicts_with", []))
    if conflicts:
        action = "conflict_pending"
    elif memory.status.value == "pending":
        action = "pending"
    elif memory.source_message_id == message_id:
        action = "created"
    else:
        action = "evidence_merged"
    return {
        "id": str(memory.id),
        "content": memory.content,
        "status": memory.status.value,
        "action": action,
        "conflicts_with": conflicts,
    }


async def record_extraction_completed(
    repository: ConversationRepository,
    job: ExtractionJob,
    memories: Sequence[Memory],
) -> None:
    run_id = _run_id_for(job)
    if run_id is None:
        return
    await repository.add_run_event(
        run_id,
        sequence=await repository.next_run_event_sequence(run_id),
        event_type="memory.extraction_completed",
        payload={
            "changes": [
                _memory_change(memory, job.message_id) for memory in memories
            ]
        },
    )


async def record_extraction_failed(
    repository: ConversationRepository, job: ExtractionJob
) -> None:
    run_id = _run_id_for(job)
    if run_id is None:
        return
    await repository.add_run_event(
        run_id,
        sequence=await repository.next_run_event_sequence(run_id),
        event_type="memory.extraction_failed",
        payload={
            "message": "记忆提取失败，请稍后重试",
            "attempts": job.attempts,
        },
    )


async def claim_next_job(
    *, worker_id: str | None = None, lease_seconds: int = JOB_LEASE_SECONDS
):
    worker_id = worker_id or f"worker-{uuid4()}"
    now = datetime.now(timezone.utc)
    stale_before = now - timedelta(seconds=lease_seconds)
    async with SessionFactory() as session, session.begin():
        job = await session.scalar(
            select(ExtractionJob)
            .where(
                or_(
                    and_(
                        ExtractionJob.status == ExtractionJobStatus.QUEUED,
                        ExtractionJob.available_at <= now,
                    ),
                    and_(
                        ExtractionJob.status == ExtractionJobStatus.RUNNING,
                        ExtractionJob.locked_at.is_not(None),
                        ExtractionJob.locked_at <= stale_before,
                    ),
                )
            )
            .order_by(
                case(
                    (ExtractionJob.status == ExtractionJobStatus.QUEUED, 0),
                    else_=1,
                ),
                ExtractionJob.created_at,
            )
            .options(selectinload(ExtractionJob.message))
            .with_for_update(skip_locked=True)
        )
        if job is None:
            return None
        job.status = ExtractionJobStatus.RUNNING
        job.attempts += 1
        job.started_at = now
        job.locked_at = now
        job.locked_by = worker_id
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
            if job.payload.get("job_type") == "embedding_backfill":
                memory_id = UUID(str(job.payload["memory_id"]))
                memory = await session.get(Memory, memory_id)
                if memory and memory.embedding is None:
                    business = get_business_config()
                    embedding = await create_embedding_model(
                        config=business
                    ).aembed_query(memory.content)
                    if len(embedding) != business.models.embedding_dimensions:
                        raise ValueError("Embedding 维度与配置不一致")
                    memory.embedding = embedding
                    metadata = dict(memory.metadata_)
                    metadata["embedding_status"] = "ready"
                    memory.metadata_ = metadata
            else:
                memories = await MemoryExtractor(session).extract_and_store(
                    user_id=job.user_id, message=job.message
                )
                repository = ConversationRepository(session)
                for memory in memories:
                    if memory.metadata_.get("embedding_status") == "pending":
                        await repository.queue_embedding_job(
                            memory=memory,
                            message_id=job.message_id,
                            conversation_id=job.conversation_id,
                            run_id=job.payload.get("run_id"),
                        )
                await record_extraction_completed(repository, job, memories)
            job.status = ExtractionJobStatus.COMPLETED
            job.finished_at = datetime.now(timezone.utc)
            job.locked_at = None
            job.locked_by = None
            job.last_error = None
        except Exception as error:  # noqa: BLE001 - mark the durable job as failed
            # Extraction and memory writes share one transaction. Roll back any
            # partial candidates before recording only the retry state.
            await session.rollback()
            job = await session.scalar(
                select(ExtractionJob).where(ExtractionJob.id == job_id)
            )
            if job is None:
                return True
            mark_job_failure(
                job,
                error,
                now=datetime.now(timezone.utc),
                max_retries=get_business_config().memory.max_retries,
            )
            if (
                job.status is ExtractionJobStatus.FAILED
                and job.payload.get("job_type") != "embedding_backfill"
            ):
                await record_extraction_failed(ConversationRepository(session), job)
        await session.commit()
    return True


async def worker_loop(poll_seconds: float = 2.0) -> None:
    while True:
        processed = await process_one()
        if not processed:
            await asyncio.sleep(poll_seconds)


if __name__ == "__main__":
    asyncio.run(worker_loop())
