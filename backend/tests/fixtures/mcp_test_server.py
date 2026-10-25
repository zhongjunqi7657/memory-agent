"""Real SDK stdio fixture: never requires credentials or a model."""

import asyncio
import os
import sys
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

server = FastMCP("memory-agent-test")
marker = Path(sys.argv[1])
marker.write_text(str(os.getpid()), encoding="utf-8")
if len(sys.argv) > 2 and sys.argv[2] == "hang-init":
    import time
    time.sleep(30)


@server.tool(name="remote.echo")
async def echo(value: str) -> dict[str, Any]:
    """Echo a value and expose a call count for authorization tests."""
    return {"value": value, "pid": os.getpid()}


@server.tool()
async def secret_env() -> dict[str, Any]:
    """Report whether an unlisted secret was inherited."""
    return {"secret": os.environ.get("MCP_TEST_SECRET"), "allowed": os.environ.get("MCP_TEST_ALLOWED")}


@server.tool()
async def fail() -> str:
    """Return an MCP business failure."""
    raise ValueError("expected business error")


@server.tool()
async def slow() -> str:
    """Wait long enough for timeout and cancellation."""
    await asyncio.sleep(30)
    return "late"


@server.tool()
async def crash() -> str:
    """Exit the process while a request is in flight."""
    os._exit(3)


@server.resource("fixture://stdio-guide")
def guide() -> str:
    return "stdio resource data"


@server.prompt()
def greeting(name: str) -> str:
    return f"Hello {name} from stdio"


if __name__ == "__main__":
    if len(sys.argv) > 2 and sys.argv[2] == "idle-exit":
        import threading
        threading.Timer(2, lambda: os._exit(4)).start()
    server.run(transport="stdio")
