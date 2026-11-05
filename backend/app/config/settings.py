"""Runtime settings loaded from environment variables only."""

from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ENV_FILE = Path(__file__).resolve().parents[3] / ".env"


class Settings(BaseSettings):
    database_url: str = (
        "postgresql+asyncpg://memory_agent:memory_agent@localhost:5432/memory_agent"
    )
    dashscope_api_key: str | None = None
    dashscope_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    app_env: str = "local"
    app_host: str = "127.0.0.1"
    app_port: int = Field(default=8000, ge=1, le=65535)
    app_cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"
    demo_shared_password: str | None = None
    local_api_token: str | None = Field(default=None, repr=False)
    app_data_dir: Path = Path(__file__).resolve().parents[3] / ".runtime"

    @field_validator("app_data_dir")
    @classmethod
    def absolute_data_directory(cls, value: Path) -> Path:
        if not value.is_absolute():
            raise ValueError("APP_DATA_DIR 必须使用绝对路径")
        return value.resolve()

    model_config = SettingsConfigDict(
        env_file=ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
