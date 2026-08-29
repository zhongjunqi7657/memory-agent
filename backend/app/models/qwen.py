"""Qwen adapters through the OpenAI-compatible DashScope API."""

from langchain_openai import ChatOpenAI, OpenAIEmbeddings

from app.config.business import BusinessConfig, get_business_config
from app.config.settings import Settings, get_settings


def create_chat_model(
    *, settings: Settings | None = None, config: BusinessConfig | None = None
) -> ChatOpenAI:
    runtime = settings or get_settings()
    business = config or get_business_config()
    if not runtime.dashscope_api_key:
        raise RuntimeError(
            "DASHSCOPE_API_KEY is required to create the Qwen chat model"
        )
    return ChatOpenAI(
        api_key=runtime.dashscope_api_key,
        base_url=runtime.dashscope_base_url,
        model=business.models.chat_model,
        temperature=0.3,
    )


def create_embedding_model(
    *, settings: Settings | None = None, config: BusinessConfig | None = None
) -> OpenAIEmbeddings:
    runtime = settings or get_settings()
    business = config or get_business_config()
    if not runtime.dashscope_api_key:
        raise RuntimeError("DASHSCOPE_API_KEY is required to create Qwen embeddings")
    return OpenAIEmbeddings(
        api_key=runtime.dashscope_api_key,
        base_url=runtime.dashscope_base_url,
        model=business.models.embedding_model,
        dimensions=business.models.embedding_dimensions,
    )
