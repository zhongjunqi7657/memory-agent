from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.agent.checkpoint import checkpoint_lifespan
from app.api.chat import router as chat_router
from app.api.conversations import router as conversations_router
from app.api.memories import router as memories_router
from app.api.reviews import router as reviews_router
from app.api.runs import router as runs_router
from app.api.system import router as system_router
from app.config.settings import get_settings

settings = get_settings()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    async with checkpoint_lifespan(settings):
        yield

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
app.include_router(conversations_router)
app.include_router(memories_router)
app.include_router(reviews_router)
app.include_router(runs_router)
app.include_router(system_router)
