"""Configuration contract for Qwen's OpenAI-compatible adapters."""

from app.config.settings import Settings
from app.models.qwen import create_embedding_model


def test_qwen_embeddings_send_text_instead_of_openai_token_ids() -> None:
    model = create_embedding_model(
        settings=Settings(dashscope_api_key="test-key")
    )

    assert model.check_embedding_ctx_length is False
