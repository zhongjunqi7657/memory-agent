"""Atomic non-secret connection overrides; grants and tokens are never serialized."""

import json
from pathlib import Path

from app.mcp.config import MCPConfig, MCPServerConfig
from app.security.tool_secrets import contains_secret


class ConnectionStore:
    def __init__(self, path: Path):
        self.path = path
        self._overrides = []

    def load(self, base: MCPConfig) -> MCPConfig:
        if not self.path.exists():
            return base.model_copy(deep=True)
        try:
            overrides = [MCPServerConfig.model_validate(item) for item in json.loads(self.path.read_text(encoding="utf-8"))]
            merged = MCPConfig(enabled=base.enabled, allowed_commands=base.allowed_commands,
                servers=[item for item in base.servers if item.id not in {s.id for s in overrides}] + overrides)
            self._overrides = overrides
            return merged
        except (ValueError, OSError, TypeError):
            raise ValueError("MCP 连接记录无效，请检查本机连接文件") from None

    def save(self, server: MCPServerConfig):
        overrides = [item for item in self._overrides if item.id != server.id] + [server]
        payload = [item.model_dump(mode="json") for item in overrides]
        if contains_secret(payload):
            raise ValueError("连接配置不能包含密钥，请使用环境变量引用")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        try:
            temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            temporary.replace(self.path)
            self._overrides = overrides
        finally:
            temporary.unlink(missing_ok=True)
