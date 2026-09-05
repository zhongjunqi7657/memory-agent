"""Business configuration loaded from the repository TOML file."""

from __future__ import annotations

import sys
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib


class ModelConfig(BaseModel):
    chat_model: str = "qwen-plus"
    embedding_model: str = "text-embedding-v3"
    embedding_dimensions: int = Field(default=1024, ge=1)


class MemoryConfig(BaseModel):
    auto_retrieve_limit: int = Field(default=5, ge=1, le=50)
    tool_retrieve_limit: int = Field(default=10, ge=1, le=100)
    max_injected_tokens: int = Field(default=1200, ge=100)
    vector_weight: float = Field(default=0.7, ge=0, le=1)
    keyword_weight: float = Field(default=0.3, ge=0, le=1)
    max_retries: int = Field(default=3, ge=0, le=10)
    active_confidence_threshold: float = Field(default=0.85, ge=0, le=1)
    pending_confidence_threshold: float = Field(default=0.5, ge=0, le=1)


class AgentConfig(BaseModel):
    recent_turns: int = Field(default=8, ge=1, le=50)
    max_tool_rounds: int = Field(default=4, ge=0, le=10)


class SecurityConfig(BaseModel):
    max_input_chars: int = Field(default=4000, ge=100)
    secret_redaction_enabled: bool = True
    rate_limit_per_minute: int = Field(default=20, ge=1, le=600)
    daily_token_budget: int = Field(default=20000, ge=100, le=10_000_000)


class BusinessConfig(BaseModel):
    """Non-secret knobs that are safe to commit and review."""

    model_config = ConfigDict(extra="ignore")

    models: ModelConfig = ModelConfig()
    memory: MemoryConfig = MemoryConfig()
    agent: AgentConfig = AgentConfig()
    security: SecurityConfig = SecurityConfig()

    @classmethod
    def load(cls, path: Path | None = None) -> BusinessConfig:
        config_path = path or _default_config_path()
        if not config_path.exists():
            return cls()
        with config_path.open("rb") as config_file:
            return cls.model_validate(tomllib.load(config_file))


def _default_config_path() -> Path:
    return Path(__file__).resolve().parents[3] / "config.toml"


@lru_cache
def get_business_config() -> BusinessConfig:
    return BusinessConfig.load()
