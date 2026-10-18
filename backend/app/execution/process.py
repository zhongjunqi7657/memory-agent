"""Bounded native processes, with cleanup even on a Windows Selector event loop."""

import asyncio
import os
import signal
import subprocess
import threading
import time

from app.security.redaction import redact_secrets

ENVIRONMENT_KEYS = frozenset({"SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT", "PATH", "TEMP", "TMP", "TMPDIR", "LANG", "LC_ALL"})


def process_environment() -> dict[str, str]:
    environment = {key: value for key, value in os.environ.items() if key.upper() in ENVIRONMENT_KEYS}
    environment.update(PYTHONIOENCODING="utf-8", PYTHONUTF8="1", PYTHONNOUSERSITE="1", GIT_TERMINAL_PROMPT="0")
    # Do not inherit PYTHONPATH, provider keys, DB credentials or user overrides.
    return environment


def _run(argv, cwd, timeout, output_limit, stop):
    read_out, write_out = os.pipe()
    read_err, write_err = os.pipe()
    streams = {"stdout": bytearray(), "stderr": bytearray()}
    truncated = False
    total = 0
    lock = threading.Lock()

    def drain(fd, name):
        nonlocal truncated, total
        with os.fdopen(fd, "rb", buffering=0) as source:
            while chunk := source.read(8192):
                with lock:
                    retained = min(len(chunk), max(0, output_limit + 512 - total))
                    streams[name].extend(chunk[:retained])
                    total += retained
                    truncated |= retained < len(chunk)

    readers = [threading.Thread(target=drain, args=(fd, name), daemon=True)
               for fd, name in ((read_out, "stdout"), (read_err, "stderr"))]
    process = None
    timed_out = False
    try:
        if os.name == "nt":
            from app.execution.windows_process import WindowsProcess

            process = WindowsProcess(argv, cwd, process_environment(), write_out, write_err)
        else:
            process = subprocess.Popen(argv, cwd=cwd, env=process_environment(), stdin=subprocess.DEVNULL,
                                       stdout=write_out, stderr=write_err, start_new_session=True)
        os.close(write_out)
        os.close(write_err)
        write_out = write_err = None
        for reader in readers:
            reader.start()
        deadline = time.monotonic() + timeout
        while process.poll() is None:
            if stop.wait(0.02):
                break
            if time.monotonic() >= deadline:
                timed_out = True
                break
        code = process.poll()
    finally:
        if process:
            if os.name == "nt":
                process.close()  # Also reaps descendants after the parent exited.
            else:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait()
        for fd in (write_out, write_err):
            if fd is not None:
                os.close(fd)
        for reader, fd in zip(readers, (read_out, read_err)):
            if reader.ident is not None:
                reader.join()
            else:
                os.close(fd)
    output = {}
    remaining = output_limit
    for name, data in streams.items():
        safe = redact_secrets(bytes(data).decode("utf-8", errors="replace")).text
        encoded = safe.encode("utf-8")
        output[name] = encoded[:remaining].decode("utf-8", errors="ignore")
        truncated |= len(encoded) > remaining
        remaining -= len(output[name].encode("utf-8"))
    return {**output,
            "exit_code": code, "timed_out": timed_out, "truncated": truncated}


async def run_process(argv: list[str], cwd: str, *, timeout: float, output_limit: int) -> dict:
    stop = threading.Event()
    worker = asyncio.create_task(asyncio.to_thread(_run, argv, cwd, timeout, output_limit, stop))
    try:
        return await asyncio.shield(worker)
    except asyncio.CancelledError:
        stop.set()
        # The owning task does not finish until its process tree and readers stop.
        await asyncio.shield(worker)
        raise
