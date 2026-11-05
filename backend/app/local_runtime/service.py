"""Own one API and one cooperative Worker; stop only their process handles."""

import asyncio
import logging
import multiprocessing
import signal
import sys
import threading
from contextlib import asynccontextmanager

from app.config.settings import get_settings


async def run_worker_until_stopped(stop, ready=None):
    logging.getLogger(__name__).info("正在初始化记忆 Worker")
    from app.jobs.worker import worker_loop
    from app.persistence.db import engine

    task = asyncio.create_task(worker_loop())
    logging.getLogger(__name__).info("记忆 Worker 轮询已启动")
    if ready is not None:
        ready.set()
    try:
        while not stop.is_set():
            if task.done():
                await task  # A failed Worker must make the launcher fail visibly.
                return
            await asyncio.sleep(0.1)
    finally:
        task.cancel()
        try:
            try:
                await task
            except asyncio.CancelledError:
                pass
        finally:
            await engine.dispose()


def worker_entry(stop, ready):
    from app.local_runtime.logging import configure_logging
    from app.runtime import event_loop

    signal.signal(signal.SIGINT, signal.SIG_IGN)
    configure_logging(get_settings(), name="worker")
    loop = event_loop()
    try:
        loop.run_until_complete(run_worker_until_stopped(stop, ready))
    except Exception:  # noqa: BLE001 - child failure is reported without raw provider errors
        logging.getLogger(__name__).error("记忆 Worker 异常退出")
        raise SystemExit(1) from None
    finally:
        loop.close()


async def wait_worker_ready(worker, ready, timeout):
    deadline = asyncio.get_running_loop().time() + timeout
    while not ready.is_set():
        if not worker.is_alive():
            raise ValueError("记忆 Worker 在初始化期间退出")
        if asyncio.get_running_loop().time() >= deadline:
            raise ValueError("记忆 Worker 初始化超时")
        await asyncio.sleep(0.1)


def serve():
    import uvicorn

    from app.config.business import get_business_config
    from app.local_runtime.logging import configure_logging
    from app.main import app

    settings = get_settings()
    runtime = get_business_config().runtime
    handler = configure_logging(settings)
    server = uvicorn.Server(uvicorn.Config(app, host=settings.app_host, port=settings.app_port,
        loop="app.runtime:event_loop", log_config=None, access_log=False,
        proxy_headers=False, timeout_graceful_shutdown=runtime.shutdown_timeout_seconds))
    context = multiprocessing.get_context("spawn")
    stop = context.Event()
    ready = context.Event()
    worker = context.Process(target=worker_entry, args=(stop, ready), name="memory-agent-worker", daemon=True)
    original_lifespan = app.router.lifespan_context
    worker_failed = False

    @asynccontextmanager
    async def managed_lifespan(application):
        nonlocal worker_failed
        async with original_lifespan(application):
            from app.agent.checkpoint import checkpoint_status
            if not checkpoint_status()["ready"]:
                raise ValueError("Checkpoint 未就绪，本机启动已中止")
            worker.start()

            async def watch_worker():
                nonlocal worker_failed
                while worker.is_alive():
                    await asyncio.sleep(0.2)
                worker_failed = True
                logging.getLogger(__name__).error("记忆 Worker 已退出，正在关闭本地应用")
                server.should_exit = True

            watcher = None
            try:
                await wait_worker_ready(worker, ready, runtime.worker_startup_timeout_seconds)
                application.state.local_worker = worker
                # Windows initializes inherited standard handles while spawning.
                # A parent thread already blocked in stdin.readline can prevent
                # the child interpreter from starting. Read only after ready.
                if not sys.stdin.isatty():
                    threading.Thread(target=read_shutdown, daemon=True).start()
                watcher = asyncio.create_task(watch_worker())
                yield
            finally:
                if watcher is not None:
                    watcher.cancel()
                    await asyncio.gather(watcher, return_exceptions=True)
                stop.set()
                await asyncio.to_thread(worker.join, runtime.shutdown_timeout_seconds)
                if worker.is_alive():
                    worker.terminate()
                    await asyncio.to_thread(worker.join)

    app.router.lifespan_context = managed_lifespan
    # A pipe provides cooperative shutdown for native launchers and smoke tests.
    # The thread owns no process handles and cannot block interpreter shutdown.
    def read_shutdown():
        try:
            if sys.stdin.readline().strip() == "stop":
                server.should_exit = True
        except (OSError, ValueError):
            pass
    listener = None
    try:
        # Bind before starting the Worker: an occupied port must have zero job effects.
        listener = server.config.bind_socket()
        server.run(sockets=[listener])
        if not server.started or worker_failed:
            raise ValueError("本地应用未正常运行，请查看本机日志")
    finally:
        if listener is not None:
            listener.close()
        app.router.lifespan_context = original_lifespan
        logging.getLogger().removeHandler(handler)
        handler.close()
