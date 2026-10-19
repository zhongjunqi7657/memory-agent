"""Single-process task handles. Directory reservations are not OS sandboxes."""

import asyncio
from contextlib import contextmanager
from dataclasses import dataclass
from uuid import UUID, uuid4

from app.workspace.paths import directories_overlap


class RunConflict(ValueError):
    pass


@dataclass
class RunningTask:
    user_id: UUID
    conversation_id: UUID
    directory: str | None
    task: asyncio.Task
    cancelling: bool = False


class RunController:
    def __init__(self):
        self._runs: dict[UUID, RunningTask] = {}
        self._closing = False

    def register(self, run_id: UUID, user_id: UUID, conversation_id: UUID, directory: str | None) -> None:
        # No awaits between conflict checking and reservation.
        if self._closing:
            raise RunConflict("应用正在关闭，不能启动新任务")
        for active in self._runs.values():
            if active.conversation_id == conversation_id:
                raise RunConflict("该会话已有运行中的任务")
            if directory and active.directory and directories_overlap(directory, active.directory):
                raise RunConflict("该目录或其父子目录已有运行中的任务")
        self._runs[run_id] = RunningTask(user_id, conversation_id, directory, asyncio.current_task())

    def release(self, run_id: UUID) -> None:
        self._runs.pop(run_id, None)

    @contextmanager
    def hold_directories(self, user_id: UUID, *directories: str):
        """Serialize user workspace operations against live tasks and each other."""
        handles = []
        try:
            for directory in dict.fromkeys(directories):
                handle = uuid4()
                self.register(handle, user_id, uuid4(), directory)
                handles.append(handle)
            yield
        finally:
            for handle in handles:
                self.release(handle)

    def cancel(self, run_id: UUID, user_id: UUID) -> bool:
        active = self._runs.get(run_id)
        if active is None or active.user_id != user_id:
            return False
        if not active.cancelling:
            active.cancelling = True
            active.task.cancel()
        return True

    async def close(self) -> None:
        self._closing = True
        tasks = [active.task for active in self._runs.values()]
        for run_id, active in list(self._runs.items()):
            self.cancel(run_id, active.user_id)
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)


# CLI and tests without ASGI lifespan still share one reservation registry.
default_controller = RunController()
