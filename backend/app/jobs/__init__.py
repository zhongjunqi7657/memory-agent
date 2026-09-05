"""Persistent background jobs."""

__all__ = ["process_one", "worker_loop"]


def __getattr__(name: str):
    if name == "process_one":
        from app.jobs.worker import process_one

        return process_one
    if name == "worker_loop":
        from app.jobs.worker import worker_loop

        return worker_loop
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
