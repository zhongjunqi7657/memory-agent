import pytest
from pydantic import ValidationError

from app.mcp.config import MCPConfig, MCPServerConfig


def test_http_config_requires_explicit_destination_and_secret_references():
    server = MCPServerConfig(id="web", transport="streamable_http", url="https://example.com/mcp",
                             auth={"type": "bearer", "token_env": "MCP_WEB_TOKEN"})
    assert MCPConfig(servers=[server]).servers[0].command is None
    assert "MCP_WEB_TOKEN" in server.model_dump_json()


@pytest.mark.parametrize("changes", [
    {"url": "http://example.com/mcp"},
    {"url": "https://user:password@example.com/mcp"},
    {"url": "https://example.com/mcp?token=secret"},
    {"url": "file:///tmp/mcp"},
    {"auth": {"type": "bearer"}},
    {"auth": {"type": "none", "token_env": "TOKEN"}},
    {"command": "python"},
])
def test_invalid_http_configuration_fails_closed(changes):
    with pytest.raises(ValidationError):
        MCPServerConfig.model_validate({"id": "web", "transport": "streamable_http",
                                        "url": "https://example.com/mcp", **changes})


def test_http_loopback_fixture_allowed_and_stdio_still_requires_command():
    MCPServerConfig(id="fixture", transport="streamable_http", url="http://127.0.0.1:8001/mcp")
    with pytest.raises(ValidationError):
        MCPServerConfig(id="missing")


def test_oauth_only_supports_explicit_public_pkce_client_and_loopback_callback():
    data = {"type": "oauth", "oauth": {"client_id": "client", "authorization_url": "https://id.example/authorize",
             "token_url": "https://id.example/token", "redirect_uri": "http://127.0.0.1:5173/"}}
    server = MCPServerConfig(id="web", transport="streamable_http", url="https://example.com/mcp", auth=data)
    assert server.auth.oauth.client_id == "client"
    data["oauth"]["redirect_uri"] = "https://other.example/callback"
    with pytest.raises(ValidationError):
        MCPServerConfig(id="web", transport="streamable_http", url="https://example.com/mcp", auth=data)
