"""Operational health and non-sensitive runtime configuration endpoints."""

from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.checkpoint import checkpoint_status
from app.config.business import get_business_config
from app.config.settings import get_settings
from app.persistence.db import get_session

router = APIRouter(tags=["system"])


@router.get("/health")
async def health(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> JSONResponse:
    try:
        await session.scalar(text("SELECT 1"))
        database = "available"
        status_code = 200
    except Exception:  # noqa: BLE001 - do not expose connection details
        database = "unavailable"
        status_code = 503
    return JSONResponse(
        status_code=status_code,
        content={
            "status": "ok" if status_code == 200 else "degraded",
            "service": "memory-agent-api",
            "database": database,
            "checkpoint": checkpoint_status(),
        },
    )


@router.get("/config")
async def public_config() -> dict[str, object]:
    settings = get_settings()
    business = get_business_config()
    return {
        "environment": settings.app_env,
        "provider_configured": bool(settings.dashscope_api_key),
        "models": {
            "chat": business.models.chat_model,
            "embedding": business.models.embedding_model,
            "embedding_dimensions": business.models.embedding_dimensions,
        },
        "memory": {
            "auto_retrieve_limit": business.memory.auto_retrieve_limit,
            "weights": {
                "vector": business.memory.vector_weight,
                "keyword": business.memory.keyword_weight,
                "recency": business.memory.recency_weight,
                "importance": business.memory.importance_weight,
                "type": business.memory.type_weight,
            },
        },
        "agent": {
            "context_token_budget": business.agent.context_token_budget,
            "max_tool_rounds": business.agent.max_tool_rounds,
        },
        "checkpoint": checkpoint_status(),
    }
