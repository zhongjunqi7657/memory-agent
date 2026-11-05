from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.agent.checkpoint import checkpoint_lifespan
from app.api.approvals import router as approvals_router
from app.api.chat import router as chat_router
from app.api.conversations import router as conversations_router
from app.api.local_dialogs import router as local_dialogs_router
from app.api.mcp import router as mcp_router
from app.api.mcp_connections import router as mcp_connections_router
from app.api.memories import router as memories_router
from app.api.projects import router as projects_router
from app.api.reviews import router as reviews_router
from app.api.runs import router as runs_router
from app.api.system import router as system_router
from app.api.workspaces import router as workspaces_router
from app.config.business import business_config_path, get_business_config
from app.config.settings import get_settings
from app.execution.controller import RunController
from app.execution.lease import runtime_lease
from app.execution.lifecycle import recover_runs
from app.mcp.manager import MCPConnectionManager
from app.permission.approvals import ApprovalBroker
from app.persistence.db import database_lifespan
from app.security.local_access import LocalAccessMiddleware, validate_local_settings

settings = get_settings()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    business = get_business_config()
    validate_local_settings(settings, local_enabled=business.local.enabled)
    async with database_lifespan(), runtime_lease(), checkpoint_lifespan(settings):
        business = get_business_config()
        manager = MCPConnectionManager(business.mcp, default_timeout_seconds=business.tools.default_timeout_seconds,
                                       cwd=str(business_config_path().parent),
                                       store_path=settings.app_data_dir / "mcp-connections.json")
        _app.state.mcp_manager = manager
        controller = RunController()
        _app.state.run_controller = controller
        _app.state.approval_broker = ApprovalBroker()
        try:
            await recover_runs()
            await manager.start()
            yield
        finally:
            try:
                await controller.close()
            finally:
                await manager.close()

app = FastAPI(
    title="Memory Agent API",
    version="0.1.0",
    lifespan=lifespan,
)
app.add_middleware(LocalAccessMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        origin.strip()
        for origin in settings.app_cors_origins.split(",")
        if origin.strip()
    ],
    allow_credentials=False,
    allow_methods=["GET", "POST", "PATCH"],
    allow_headers=["Content-Type", "Authorization", "X-Local-Token"],
    expose_headers=["WWW-Authenticate"],
)
app.include_router(chat_router)
app.include_router(approvals_router)
app.include_router(conversations_router)
app.include_router(memories_router)
app.include_router(mcp_router)
app.include_router(mcp_connections_router)
app.include_router(reviews_router)
app.include_router(runs_router)
app.include_router(projects_router)
app.include_router(local_dialogs_router)
app.include_router(system_router)
app.include_router(workspaces_router)

frontend_dist = Path(__file__).resolve().parents[2] / "frontend" / "dist"
if frontend_dist.is_dir():
    # Keep the frontend at the root required by registered MCP OAuth callbacks.
    app.mount("/", StaticFiles(directory=frontend_dist, html=True), name="ui")
