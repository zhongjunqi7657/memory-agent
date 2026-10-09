"""Audit output boundaries for the tool executor."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Protocol


class AuditSink(Protocol):
    async def emit(self, event_type: str, payload: dict[str, object]) -> None: ...


class CallbackAuditSink:
    def __init__(
        self,
        callback: Callable[[str, dict[str, object]], Awaitable[None]],
    ) -> None:
        self._callback = callback

    async def emit(self, event_type: str, payload: dict[str, object]) -> None:
        await self._callback(event_type, payload)


class NullAuditSink:
    async def emit(self, event_type: str, payload: dict[str, object]) -> None:
        return None
