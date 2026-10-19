"""Windows SDK loop bridge when the API uses psycopg's Selector loop."""

import asyncio
from threading import Event, Thread


class SDKLoopBridge:
    def __init__(self) -> None:
        self.loop = None
        self._ready = Event()
        self._thread = Thread(target=self._run, name="mcp-sdk-loop", daemon=True)

    def _run(self) -> None:
        loop = asyncio.ProactorEventLoop()
        asyncio.set_event_loop(loop)
        self.loop = loop
        self._ready.set()
        try:
            loop.run_forever()
        finally:
            loop.run_until_complete(loop.shutdown_asyncgens())
            loop.run_until_complete(loop.shutdown_default_executor())
            loop.close()

    async def start(self) -> None:
        self._thread.start()
        await asyncio.to_thread(self._ready.wait)

    async def run(self, coroutine):
        return await asyncio.wrap_future(asyncio.run_coroutine_threadsafe(coroutine, self.loop))

    async def close(self) -> None:
        self.loop.call_soon_threadsafe(self.loop.stop)
        await asyncio.to_thread(self._thread.join)
