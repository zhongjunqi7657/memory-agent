"""Runtime loop compatible with psycopg Checkpoint on Windows."""

import asyncio
import sys


def event_loop() -> asyncio.AbstractEventLoop:
    # MCP owns a separate Proactor loop when this Windows Selector loop is used.
    return asyncio.SelectorEventLoop() if sys.platform == "win32" else asyncio.new_event_loop()
