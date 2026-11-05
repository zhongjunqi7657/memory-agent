"""Actual Windows/POSIX API + spawned Worker against a fresh test database."""

import asyncio
import os
import signal
import subprocess
import sys
import time
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import select, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.local_runtime.checks import REPOSITORY_ROOT
from app.persistence.models import (
    ExtractionJob,
    ExtractionJobStatus,
    Memory,
    MessageRole,
    RunEvent,
    RunStatus,
)
from app.persistence.repositories import ConversationRepository
from tests.fixtures.local_model_server import MEMORY_CONTENT, model_server
from tests.test_mcp_stdio_integration import process_alive


@pytest.fixture
async def local_database(postgres_factory):
    # postgres_factory validates the externally supplied test target first.
    source = os.environ["TEST_DATABASE_URL"]
    assert (make_url(source).database or "").endswith("_test")
    name = f"local_delivery_{uuid4().hex}_test"
    engine = create_async_engine(source, isolation_level="AUTOCOMMIT")
    try:
        async with engine.connect() as connection:
            await connection.execute(text(f'CREATE DATABASE "{name}"'))
        yield make_url(source).set(database=name).render_as_string(hide_password=False)
    finally:
        async with engine.connect() as connection:
            await connection.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        await engine.dispose()


def free_port():
    import socket
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


@pytest.fixture
def local_model():
    with model_server() as server:
        yield server


async def verify_worker_persists_memory(database_url, client, token, model_calls):
    engine = create_async_engine(database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            repository = ConversationRepository(session)
            user = await repository.get_or_create_user("delivery-worker-test")
            conversation = await repository.create_conversation(user.id)
            message = await repository.add_message(conversation.id, role=MessageRole.USER,
                content="我偏好先理解原理再看代码", sequence=1)
            run = await repository.create_run(conversation.id)
            await repository.finish_run(run, status=RunStatus.COMPLETED)
            job = await repository.queue_extraction_job(user.id, conversation.id, message.id,
                                                       payload={"run_id": str(run.id)})
            job_id, run_id = job.id, run.id
            await session.commit()
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            async with factory() as session:
                job = await session.get(ExtractionJob, job_id)
                if job.status in {ExtractionJobStatus.COMPLETED, ExtractionJobStatus.FAILED}:
                    assert job.status == ExtractionJobStatus.COMPLETED, job.last_error
                    assert job.attempts == 1 and job.finished_at and job.locked_by is None
                    memory = await session.scalar(select(Memory).where(Memory.user_id == user.id))
                    assert memory.content == MEMORY_CONTENT
                    assert memory.metadata_["embedding_status"] == "ready"
                    assert len(memory.embedding) == 1024
                    event = await session.scalar(select(RunEvent).where(RunEvent.run_id == run_id))
                    assert event.event_type == "memory.extraction_completed"
                    assert event.payload["changes"][0]["id"] == str(memory.id)
                    break
            await asyncio.sleep(0.1)
        else:
            pytest.fail("spawned Worker did not complete the durable job")
        response = await client.get("/v1/memories?user_key=delivery-worker-test", headers={"X-Local-Token": token})
        assert response.status_code == 200
        assert any(row["content"] == MEMORY_CONTENT for row in response.json())
        assert [path for path, _body in model_calls] == ["/v1/chat/completions", "/v1/embeddings"]
    finally:
        await engine.dispose()


@pytest.mark.parametrize("worker_failure", [False, True])
async def test_native_launcher_auth_shutdown_and_worker_failure(local_database, tmp_path, worker_failure, local_model):
    token = "local-smoke-token-" + uuid4().hex
    port = free_port()
    environment = {**os.environ, "DATABASE_URL": local_database, "APP_ENV": "local",
        "LOCAL_API_TOKEN": token, "APP_HOST": "127.0.0.1", "APP_PORT": str(port),
        "APP_DATA_DIR": str(tmp_path / "data"), "DASHSCOPE_API_KEY": "fixture-unused-no-model-calls",
        "DASHSCOPE_BASE_URL": local_model[0],
        "NO_PROXY": "127.0.0.1,localhost,::1",
        "MEMORY_AGENT_CONFIG": str(REPOSITORY_ROOT / "config.toml.example"), "DEMO_SHARED_PASSWORD": ""}
    # Only the local deterministic model fixture is reachable by this Worker.
    migration = await asyncio.to_thread(subprocess.run,
        [sys.executable, "-m", "app.cli", "local", "migrate"], env=environment,
        cwd=tmp_path, capture_output=True, check=False)
    # Installed editable package must support invoking from outside the repository.
    assert migration.returncode == 0, migration.stdout.decode(errors="replace")
    output = (tmp_path / "launcher.log").open("w", encoding="utf-8")
    process = await asyncio.to_thread(subprocess.Popen, [sys.executable, "-m", "app.cli", "local", "start"],
        env=environment, cwd=tmp_path, stdin=subprocess.PIPE, stdout=output, stderr=output, text=True)
    worker_pid = None
    try:
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}", trust_env=False) as client:
            deadline = time.monotonic() + 90
            while time.monotonic() < deadline:
                assert process.poll() is None, (tmp_path / "launcher.log").read_text(encoding="utf-8")
                try:
                    response = await client.get("/health", headers={"X-Local-Token": token})
                    if response.status_code == 200:
                        break
                except httpx.TransportError:
                    pass
                await asyncio.sleep(0.1)
            else:
                pytest.fail("native launcher startup timed out")
            assert response.json()["checkpoint"]["ready"]
            worker_pid = response.json()["local_runtime"]["worker_pid"]
            assert worker_pid and process_alive(worker_pid)
            assert (await client.get("/config")).status_code == 401
            assert (await client.get("/")).status_code == 200
            callback = await client.get("/?code=fixture-code&state=fixture-state")
            assert callback.status_code == 200
            assert "fixture-code" not in callback.text
            assert token not in (await client.get("/config", headers={"X-Local-Token": token})).text
            assert (await client.post("/v1/projects", headers={"X-Local-Token": token, "Origin": "https://evil.example"})).status_code == 403
            # A second instance cannot recover or interrupt the first instance's runs.
            second = await asyncio.to_thread(subprocess.run,
                [sys.executable, "-m", "app.cli", "local", "start"],
                env={**environment, "APP_PORT": str(free_port()), "APP_DATA_DIR": str(tmp_path / "second")},
                cwd=tmp_path, capture_output=True, timeout=30, check=False)
            assert second.returncode != 0
            assert (await client.get("/health", headers={"X-Local-Token": token})).status_code == 200
            for command in (["migrate"], ["backup", "--output", str(tmp_path / "blocked.dump")]):
                maintenance = await asyncio.to_thread(subprocess.run,
                    [sys.executable, "-m", "app.cli", "local", *command], env=environment,
                    cwd=tmp_path, capture_output=True, timeout=15, check=False)
                assert maintenance.returncode != 0
                assert token.encode() not in maintenance.stdout
            assert not (tmp_path / "blocked.dump").exists()
            await verify_worker_persists_memory(local_database, client, token, local_model[1])
        if worker_failure:
            os.kill(worker_pid, signal.SIGTERM)
        else:
            process.stdin.write("stop\n")
            process.stdin.flush()
        code = await asyncio.to_thread(process.wait, timeout=30)
        assert (code != 0) is worker_failure
        assert not process_alive(worker_pid)
    finally:
        if process.poll() is None:
            process.stdin.write("stop\n")
            process.stdin.flush()
            try:
                await asyncio.to_thread(process.wait, timeout=20)
            except subprocess.TimeoutExpired:
                process.kill()
                await asyncio.to_thread(process.wait)
        process.stdin.close()
        output.close()
