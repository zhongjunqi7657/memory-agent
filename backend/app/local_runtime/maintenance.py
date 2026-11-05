"""Keep the maintenance lease until its native command has actually stopped."""

import asyncio


async def run_maintenance_command(command, *args, **kwargs):
    task = asyncio.create_task(asyncio.to_thread(command, *args, **kwargs))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        # Cancelling to_thread does not terminate its subprocess. Wait before
        # releasing the database lease or deleting an archive it still writes.
        try:
            await asyncio.shield(task)
        except Exception:  # noqa: BLE001, S110 - preserve cancellation after command cleanup
            pass
        raise
