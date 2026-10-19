"""Validated stdio server configuration and explicit capability declarations."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.mcp.naming import validate_server_id
from app.permission.policy import KNOWN_CAPABILITIES


class CapabilityConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    capabilities: frozenset[str]

    @field_validator("capabilities")
    @classmethod
    def known_capabilities(cls, value):
        if value - KNOWN_CAPABILITIES:
            raise ValueError("未知工具能力")
        return value


class MCPServerConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    enabled: bool = True
    transport: Literal["stdio"] = "stdio"
    command: str = Field(min_length=1)
    args: list[str] = Field(default_factory=list)
    env_allowlist: list[str] = Field(default_factory=list)
    allowed_tools: frozenset[str] | None = None
    command_timeout_seconds: float = Field(default=10, gt=0, le=120)
    default_permissions: CapabilityConfig | None = None
    tool_permissions: dict[str, CapabilityConfig] = Field(default_factory=dict)

    @field_validator("id")
    @classmethod
    def valid_id(cls, value):
        return validate_server_id(value)

    def permissions_for(self, remote_name: str) -> frozenset[str]:
        declaration = self.tool_permissions.get(remote_name, self.default_permissions)
        if declaration is None:
            raise ValueError(f"MCP 工具未声明权限: {self.id}/{remote_name}")
        return declaration.capabilities


class MCPConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = True
    allowed_commands: list[str] = Field(default_factory=lambda: ["python", "node", "npx", "uvx"])
    servers: list[MCPServerConfig] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_servers(self):
        ids = [server.id for server in self.servers]
        if len(ids) != len(set(ids)):
            raise ValueError("MCP Server ID 重复")
        for server in self.servers:
            if server.command not in self.allowed_commands:
                raise ValueError(f"MCP 命令未允许: {server.id}")
        return self
