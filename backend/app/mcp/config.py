"""Validated MCP transports, credential references and capability declarations."""

from __future__ import annotations

import re
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.mcp.http_security import loopback, validate_http_url
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


class OAuthConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    client_id: str = Field(min_length=1)
    authorization_url: str
    token_url: str
    redirect_uri: str
    scope: str = ""
    authorization_timeout_seconds: float = Field(default=300, gt=0, le=900)
    default_token_ttl_seconds: float = Field(default=3600, gt=0, le=86400)

    @field_validator("authorization_url", "token_url", "redirect_uri")
    @classmethod
    def valid_url(cls, value):
        return validate_http_url(value)

    @field_validator("redirect_uri")
    @classmethod
    def local_callback(cls, value):
        if not loopback(urlsplit(value).hostname):
            raise ValueError("OAuth 回调仅支持本机前端地址")
        return value


class MCPAuthConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["none", "bearer", "oauth"] = "none"
    token_env: str | None = None
    oauth: OAuthConfig | None = None

    @field_validator("token_env")
    @classmethod
    def valid_env(cls, value):
        if value is not None and not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value):
            raise ValueError("凭据只能引用环境变量名称")
        return value

    @model_validator(mode="after")
    def explicit_auth(self):
        if ((self.type == "bearer") != (self.token_env is not None)
                or (self.type == "oauth") != (self.oauth is not None)):
            raise ValueError("鉴权模式与凭据引用不匹配")
        return self


class MCPServerConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    enabled: bool = True
    transport: Literal["stdio", "streamable_http"] = "stdio"
    command: str | None = Field(default=None, min_length=1)
    url: str | None = None
    auth: MCPAuthConfig = Field(default_factory=MCPAuthConfig)
    args: list[str] = Field(default_factory=list)
    env_allowlist: list[str] = Field(default_factory=list)
    allowed_tools: frozenset[str] | None = None
    command_timeout_seconds: float = Field(default=10, gt=0, le=120)
    default_permissions: CapabilityConfig | None = None
    tool_permissions: dict[str, CapabilityConfig] = Field(default_factory=dict)
    allowed_resources: frozenset[str] | None = None
    allowed_prompts: frozenset[str] | None = None

    @field_validator("url")
    @classmethod
    def valid_url(cls, value):
        return validate_http_url(value) if value is not None else value

    @model_validator(mode="after")
    def transport_fields(self):
        if self.transport == "stdio":
            if not self.command or self.url is not None or self.auth.type != "none":
                raise ValueError("stdio 需要命令且不接受 HTTP 地址或鉴权")
        elif self.url is None or self.command is not None or self.args or self.env_allowlist:
            raise ValueError("HTTP 需要地址且不接受进程命令与环境继承")
        return self

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
            if server.transport == "stdio" and server.command not in self.allowed_commands:
                raise ValueError(f"MCP 命令未允许: {server.id}")
        return self
