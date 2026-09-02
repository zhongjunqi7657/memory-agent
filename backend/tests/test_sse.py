"""Fast SSE transport checks that do not require a database or model API."""

import asyncio
import json
from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient

import app.api.chat as chat_api
from app.main import app
from app.persistence.db import get_session


class StubAgentService:
    def __init__(self, _session) -> None:
        pass

    async def chat(self, **kwargs) -> None:
        publish = kwargs["event_sink"]
        await publish({"event_type": "run.started", "payload": {}})
        await publish({"event_type": "run.completed", "payload": {}})


@pytest.mark.asyncio
async def test_stream_finishes_immediately_after_completed_event(monkeypatch) -> None:
    async def override_session() -> AsyncIterator[object]:
        yield object()

    app.dependency_overrides[get_session] = override_session
    monkeypatch.setattr(chat_api, "AgentService", StubAgentService)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await asyncio.wait_for(
                client.post(
                    "/v1/chat/stream",
                    json={"user_key": "test-user", "content": "你好"},
                ),
                timeout=1,
            )
    finally:
        app.dependency_overrides.clear()

    events = [
        json.loads(line.removeprefix("data: "))
        for line in response.text.splitlines()
        if line.startswith("data: ")
    ]
    assert [event["event_type"] for event in events] == [
        "run.started",
        "run.completed",
    ]
