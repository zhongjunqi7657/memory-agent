"""Commit events before delivery; reconcile cancellation and restart without replay."""

from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.execution.controller import RunController
from app.persistence.approvals import Approval
from app.persistence.models import Run, RunStatus
from app.persistence.repositories import ConversationRepository
from app.security.redaction import redact_secrets

EventSink = Callable[[dict[str, object]], Awaitable[None]]
ACTIVE_STATUSES = (RunStatus.QUEUED, RunStatus.RUNNING, RunStatus.WAITING_APPROVAL)


class RunLifecycle:
    def __init__(self, session: AsyncSession, controller: RunController, sink: EventSink | None):
        self.session = session
        self.controller = controller
        self.sink = sink
        self.run_id = uuid4()
        self.registered = False
        self.sequence = 0
        self.pending: list[dict[str, object]] = []

    def reserve(self, user_id: UUID, conversation_id: UUID, directory: str | None) -> None:
        self.controller.register(self.run_id, user_id, conversation_id, directory)
        self.registered = True

    async def emit(self, event_type: str, payload: dict[str, object] | None = None, *, durable: bool = True) -> None:
        if event_type == "tool.execution.cancelled":
            # A cancelled tool may have flushed a partial memory operation.
            await self.session.rollback()
            self.pending.clear()
            self.sequence = await ConversationRepository(self.session).next_run_event_sequence(self.run_id) - 1
        self.sequence += 1
        event = {"run_id": str(self.run_id), "sequence": self.sequence,
                 "event_type": event_type, "payload": payload or {}}
        await ConversationRepository(self.session).add_run_event(
            self.run_id, sequence=self.sequence, event_type=event_type, payload=event["payload"],
        )
        self.pending.append(event)
        if durable:
            await self.session.commit()
            pending, self.pending = self.pending, []
            for item in pending:
                if self.sink:
                    await self.sink(item)

    async def terminate(self, status: RunStatus, error: BaseException) -> None:
        # Roll back only the unfinished operation. Earlier durable events remain.
        await self.session.rollback()
        self.pending.clear()
        run = await self.session.get(Run, self.run_id)
        if run is None or run.status not in ACTIVE_STATUSES:
            return
        repository = ConversationRepository(self.session)
        self.sequence = await repository.next_run_event_sequence(self.run_id) - 1
        safe_error = redact_secrets(str(error)).text[:500]
        await repository.finish_run(run, status=status, error_message=safe_error or None)
        message = "任务已取消" if status is RunStatus.CANCELLED else "任务运行失败"
        await self.emit(f"run.{status.value}", {"message": message})


async def interrupt_stale_runs(session: AsyncSession) -> int:
    """Single application instance only. Never recreate tasks or approval grants."""
    runs = list(await session.scalars(select(Run).where(Run.status.in_(ACTIVE_STATUSES)).with_for_update()))
    repository = ConversationRepository(session)
    for run in runs:
        run.status = RunStatus.INTERRUPTED
        run.finished_at = datetime.now(timezone.utc)
        run.error_message = "应用已重启，任务未自动恢复"
        await repository.add_run_event(
            run.id, sequence=await repository.next_run_event_sequence(run.id),
            event_type="run.interrupted", payload={"message": run.error_message},
        )
    await session.execute(update(Approval).where(Approval.status == "pending").values(status="interrupted"))
    await session.commit()
    return len(runs)


async def recover_runs() -> None:
    from app.persistence.db import get_session_factory

    async with get_session_factory()() as session:
        await interrupt_stale_runs(session)
