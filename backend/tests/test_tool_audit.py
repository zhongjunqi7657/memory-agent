import pytest

from app.audit.sink import CallbackAuditSink, NullAuditSink


@pytest.mark.asyncio
async def test_callback_and_null_audit_sinks() -> None:
    events: list[tuple[str, dict[str, object]]] = []

    async def callback(event_type: str, payload: dict[str, object]) -> None:
        events.append((event_type, payload))

    await CallbackAuditSink(callback).emit("event", {"safe": True})
    await NullAuditSink().emit("ignored", {})

    assert events == [("event", {"safe": True})]
