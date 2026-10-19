"""Directory identity and in-process task exclusion, without model dependencies."""

import asyncio
import os
import subprocess
from uuid import uuid4

import pytest

from app.execution.controller import RunConflict, RunController
from app.workspace.paths import DirectoryError, canonical_directory, directories_overlap


def test_directory_validation_and_overlap(tmp_path):
    child = tmp_path / "child"
    child.mkdir()
    file = child / "file.txt"
    file.write_text("data", encoding="utf-8")
    for invalid in ("", "relative", str(file), str(child / "missing")):
        with pytest.raises(DirectoryError):
            canonical_directory(invalid)
    root = canonical_directory(str(tmp_path))
    assert canonical_directory(str(child / "..")) == root
    assert directories_overlap(root, canonical_directory(str(child)))
    assert not directories_overlap(root, root + "-other")
    if os.name == "nt":
        assert canonical_directory(str(tmp_path).upper()) == root


def test_directory_links_resolve_to_same_physical_root(tmp_path):
    target, alias = tmp_path / "target", tmp_path / "alias"
    target.mkdir()
    if os.name == "nt":
        subprocess.run(["cmd", "/c", "mklink", "/J", str(alias), str(target)], check=True, capture_output=True)
    else:
        alias.symlink_to(target, target_is_directory=True)
    assert canonical_directory(str(alias)) == canonical_directory(str(target))
    assert directories_overlap(canonical_directory(str(alias)), canonical_directory(str(target)))


@pytest.mark.asyncio
async def test_conflicts_cancellation_ownership_and_release(tmp_path):
    controller = RunController()
    directory = canonical_directory(str(tmp_path))
    run_id, user_id, conversation_id = uuid4(), uuid4(), uuid4()
    started = asyncio.Event()
    cleaned = asyncio.Event()

    async def run():
        controller.register(run_id, user_id, conversation_id, directory)
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            # Repeated cancellation must not interrupt cleanup.
            await asyncio.sleep(0)
            controller.release(run_id)
            cleaned.set()

    task = asyncio.create_task(run())
    await started.wait()
    try:
        for other_conversation, other_directory in (
            (conversation_id, None), (uuid4(), directory),
            (uuid4(), os.path.join(directory, "child")),
        ):
            with pytest.raises(RunConflict):
                controller.register(uuid4(), user_id, other_conversation, other_directory)
        assert not controller.cancel(run_id, uuid4())
        assert controller.cancel(run_id, user_id)
        assert controller.cancel(run_id, user_id)
        with pytest.raises(asyncio.CancelledError):
            await task
        assert cleaned.is_set()
        next_id = uuid4()
        controller.register(next_id, user_id, conversation_id, directory)
        controller.release(next_id)
    finally:
        await controller.close()


@pytest.mark.asyncio
async def test_shutdown_cancels_owned_tasks_and_rejects_new_tasks():
    controller = RunController()
    ready = asyncio.Event()
    run_id = uuid4()

    async def run():
        controller.register(run_id, uuid4(), uuid4(), None)
        ready.set()
        try:
            await asyncio.Event().wait()
        finally:
            controller.release(run_id)

    task = asyncio.create_task(run())
    await ready.wait()
    await controller.close()
    assert task.cancelled()
    with pytest.raises(RunConflict):
        controller.register(uuid4(), uuid4(), uuid4(), None)
