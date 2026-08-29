"""Model provider adapters."""

from app.models.qwen import create_chat_model, create_embedding_model

__all__ = ["create_chat_model", "create_embedding_model"]
