"""Fast SSE transport checks that do not require a database or model API."""

import asyncio
import json
from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient

import app.api.chat as chat_api
from app.main import app
from app.persistence.db import SessionFactory, get_session


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


@pytest.mark.asyncio
async def test_agent_run_continues_after_stream_consumer_disconnects(monkeypatch) -> None:
    release = asyncio.Event()
    finished = asyncio.Event()
    cancelled = False

    class BlockingAgentService:
        def __init__(self, _session) -> None:
            pass

        async def chat(self, **kwargs) -> None:
            nonlocal cancelled
            publish = kwargs["event_sink"]
            await publish(
                {
                    "run_id": "00000000-0000-0000-0000-000000000001",
                    "sequence": 1,
                    "event_type": "run.started",
                    "payload": {},
                }
            )
            try:
                await release.wait()
                await publish(
                    {
                        "run_id": "00000000-0000-0000-0000-000000000001",
                        "sequence": 2,
                        "event_type": "run.completed",
                        "payload": {},
                    }
                )
            except asyncio.CancelledError:
                cancelled = True
                raise
            finally:
                finished.set()

    monkeypatch.setattr(chat_api, "AgentService", BlockingAgentService)
    response = await chat_api.chat_stream(
        chat_api.ChatRequest(user_key="test-user", content="你好"),
        SessionFactory,
    )
    iterator = response.body_iterator
    first_chunk = await anext(iterator)
    assert "run.started" in first_chunk

    await iterator.aclose()
    await asyncio.sleep(0)
    assert not cancelled

    release.set()
    await asyncio.wait_for(finished.wait(), timeout=1)
    assert not cancelled
