"""Non-secret local lifecycle thresholds."""

from pydantic import BaseModel, ConfigDict, Field


class RuntimeConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    shutdown_timeout_seconds: int = Field(default=15, ge=1, le=120)
    worker_startup_timeout_seconds: int = Field(default=60, ge=1, le=300)
