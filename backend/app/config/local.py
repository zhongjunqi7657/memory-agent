"""Non-secret limits for opt-in local execution and interactive approvals."""

from pydantic import BaseModel, ConfigDict, Field


class LocalExecutionConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    # Existing shared/demo deployments must not acquire local execution implicitly.
    enabled: bool = False
    max_file_bytes: int = Field(default=1_048_576, ge=1024, le=10_485_760)
    max_entries: int = Field(default=200, ge=1, le=5000)
    max_search_files: int = Field(default=1000, ge=1, le=10000)
    max_snapshot_bytes: int = Field(default=16_777_216, ge=1024, le=104_857_600)
    max_diff_bytes: int = Field(default=65536, ge=1024, le=1_048_576)
    max_output_bytes: int = Field(default=65536, ge=256, le=1_048_576)
    command_timeout_seconds: float = Field(default=60, gt=0, le=600)
    approval_timeout_seconds: float = Field(default=120, gt=0, le=3600)
    text_extensions: list[str] = Field(default_factory=lambda: [
        ".py", ".ts", ".tsx", ".js", ".jsx", ".json", ".toml", ".yaml", ".yml",
        ".md", ".txt", ".css", ".html", ".xml", ".sql", ".sh", ".ps1", ".ini", ".cfg",
        ".rs", ".go", ".java", ".c", ".h", ".cpp", ".cs", ".vue", ".svelte", ".csv",
    ])
