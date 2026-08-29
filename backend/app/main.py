from fastapi import FastAPI

from app.api.chat import router as chat_router
from app.api.memories import router as memories_router

app = FastAPI(
    title="Memory Agent API",
    version="0.1.0",
)
app.include_router(chat_router)
app.include_router(memories_router)


@app.get("/health", tags=["system"])
async def health() -> dict[str, str]:
    return {"status": "ok", "service": "memory-agent-api"}
