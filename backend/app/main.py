from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

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

settings = get_settings()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    async with runtime_lease(), checkpoint_lifespan(settings):
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
            await controller.close()
            await manager.close()

app = FastAPI(
    title="Memory Agent API",
    version="0.1.0",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        origin.strip()
        for origin in settings.app_cors_origins.split(",")
        if origin.strip()
    ],
    allow_credentials=False,
    allow_methods=["GET", "POST", "PATCH"],
    allow_headers=["Content-Type", "Authorization"],
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
