from datetime import datetime, timezone
from types import SimpleNamespace

from app.jobs.worker import mark_job_failure
from app.persistence.models import ExtractionJobStatus


def job(*, attempts: int):
    return SimpleNamespace(
        attempts=attempts,
        status=ExtractionJobStatus.RUNNING,
        available_at=None,
        locked_at=datetime.now(timezone.utc),
        locked_by="worker-a",
        last_error=None,
        finished_at=None,
    )


def test_failed_job_is_requeued_with_backoff_before_retry_limit() -> None:
    timestamp = datetime(2026, 1, 1, tzinfo=timezone.utc)
    claimed = job(attempts=1)

    mark_job_failure(
        claimed, RuntimeError("temporary"), now=timestamp, max_retries=3
    )

    assert claimed.status is ExtractionJobStatus.QUEUED
    assert claimed.available_at == datetime(2026, 1, 1, 0, 0, 1, tzinfo=timezone.utc)
    assert claimed.locked_at is None
    assert claimed.last_error == "temporary"


def test_failed_job_becomes_terminal_after_retry_limit() -> None:
    timestamp = datetime(2026, 1, 1, tzinfo=timezone.utc)
    claimed = job(attempts=3)

    mark_job_failure(claimed, ValueError("bad payload"), now=timestamp, max_retries=3)

    assert claimed.status is ExtractionJobStatus.FAILED
    assert claimed.finished_at == timestamp
    assert claimed.locked_by is None
