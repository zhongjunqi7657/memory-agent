"""Business configuration loaded from the repository TOML file."""

from __future__ import annotations

import logging
import os
import sys
import warnings
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.config.local import LocalExecutionConfig
from app.mcp.config import MCPConfig

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
    vector_weight: float = Field(default=0.55, ge=0, le=1)
    keyword_weight: float = Field(default=0.20, ge=0, le=1)
    recency_weight: float = Field(default=0.10, ge=0, le=1)
    importance_weight: float = Field(default=0.10, ge=0, le=1)
    type_weight: float = Field(default=0.05, ge=0, le=1)
    recency_half_life_days: int = Field(default=30, ge=1, le=3650)
    min_relevance_score: float = Field(default=0.08, ge=0, le=1)
    max_retries: int = Field(default=3, ge=0, le=10)
    active_confidence_threshold: float = Field(default=0.85, ge=0, le=1)
    pending_confidence_threshold: float = Field(default=0.5, ge=0, le=1)


class AgentConfig(BaseModel):
    recent_turns: int = Field(default=8, ge=1, le=50)
    max_tool_rounds: int = Field(default=4, ge=0, le=10)
    project_max_tool_rounds: int = Field(default=24, ge=0, le=100)
    context_token_budget: int = Field(default=6000, ge=500, le=100000)
    context_system_reserve_tokens: int = Field(default=200, ge=0, le=10000)
    summary_trigger_tokens: int = Field(default=4800, ge=200, le=100000)
    summary_refresh_messages: int = Field(default=6, ge=1, le=100)
    summary_max_chars: int = Field(default=1200, ge=100, le=10000)


class SecurityConfig(BaseModel):
    max_input_chars: int = Field(default=4000, ge=100)
    secret_redaction_enabled: bool = True
    rate_limit_per_minute: int = Field(default=20, ge=1, le=600)
    daily_token_budget: int = Field(default=20000, ge=100, le=10_000_000)


class ToolConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = True
    allowed_builtin_tools: list[str] = Field(
        default_factory=lambda: [
            "builtin.memory.search",
            "builtin.memory.timeline",
            "builtin.memory.propose_update",
        ]
    )
    default_timeout_seconds: float = Field(default=8.0, gt=0, le=120)


class BusinessConfig(BaseModel):
    """Non-secret knobs that are safe to commit and review."""

    model_config = ConfigDict(extra="ignore")

    models: ModelConfig = ModelConfig()
    memory: MemoryConfig = MemoryConfig()
    agent: AgentConfig = AgentConfig()
    security: SecurityConfig = SecurityConfig()
    tools: ToolConfig = ToolConfig()
    mcp: MCPConfig = MCPConfig()
    local: LocalExecutionConfig = LocalExecutionConfig()

    @model_validator(mode="before")
    @classmethod
    def migrate_legacy_tools(cls, value):
        if not isinstance(value, dict):
            return value
        legacy = dict(value.get("mcp", {})) if isinstance(value.get("mcp", {}), dict) else {}
        if "allowed_tools" not in legacy and "tool_timeout_seconds" not in legacy:
            return value
        warnings.warn("mcp.allowed_tools / tool_timeout_seconds 已弃用，请迁移到 [tools]",
                      DeprecationWarning, stacklevel=2)
        logging.getLogger(__name__).warning("旧 MCP 工具配置已弃用，请将 allowed_tools 和 tool_timeout_seconds 迁移到 [tools]")
        names = {"search_memory": "builtin.memory.search", "get_user_timeline": "builtin.memory.timeline",
                 "propose_memory_update": "builtin.memory.propose_update"}
        tools = dict(value.get("tools", {}))
        if "allowed_tools" in legacy:
            tools.setdefault("allowed_builtin_tools", [names.get(name, name) for name in legacy.pop("allowed_tools")])
        if "tool_timeout_seconds" in legacy:
            tools.setdefault("default_timeout_seconds", legacy.pop("tool_timeout_seconds"))
        tools.setdefault("enabled", legacy.get("enabled", True))
        return {**value, "mcp": legacy, "tools": tools}

    @classmethod
    def load(cls, path: Path | None = None) -> BusinessConfig:
        config_path = path or business_config_path()
        if not config_path.exists():
            if path is not None or os.environ.get("MEMORY_AGENT_CONFIG"):
                raise FileNotFoundError("指定业务配置文件不存在")
            return cls()
        with config_path.open("rb") as config_file:
            return cls.model_validate(tomllib.load(config_file))


def business_config_path() -> Path:
    override = os.environ.get("MEMORY_AGENT_CONFIG")
    if override:
        return Path(override).resolve()
    source = Path(__file__).resolve()
    repository = source.parents[3] / "config.toml"
    installed = source.parents[2] / "config.toml"
    return repository if repository.exists() or not installed.exists() else installed


@lru_cache
def get_business_config() -> BusinessConfig:
    return BusinessConfig.load()
