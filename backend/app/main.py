from fastapi import FastAPI

app = FastAPI(
    title="Memory Agent API",
    version="0.1.0",
)


@app.get("/health", tags=["system"])
async def health() -> dict[str, str]:
    return {"status": "ok", "service": "memory-agent-api"}

