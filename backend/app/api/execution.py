"""Resolve the application-owned runtime for HTTP and direct endpoint tests."""

from fastapi import Request

from app.execution.controller import RunController, default_controller


def run_controller(request: Request | None) -> RunController:
    return getattr(request.app.state, "run_controller", default_controller) if request else default_controller
