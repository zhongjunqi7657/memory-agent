import pytest
from pydantic import ValidationError

from app.mcp.config import MCPConfig, MCPServerConfig


@pytest.mark.parametrize("servers", [
    [{"id": "a", "command": "curl"}],
    [{"id": "a", "command": "python", "transport": "sse"}],
    [{"id": "a", "command": "python"}, {"id": "a", "command": "python"}],
    [{"id": "a", "command": "python", "default_permissions": {"capabilities": ["root"]}}],
])
def test_config_rejects_unsafe_servers(servers):
    with pytest.raises(ValidationError):
        MCPConfig(servers=servers)


def test_permission_declarations_are_explicit_and_overrides_replace_default():
    server = MCPServerConfig(id="a", command="python")
    with pytest.raises(ValueError, match="未声明权限"):
        server.permissions_for("read")
    server = MCPServerConfig(id="a", command="python",
        default_permissions={"capabilities": ["filesystem.read"]},
        tool_permissions={"write": {"capabilities": ["filesystem.write"]}})
    assert server.permissions_for("read") == frozenset({"filesystem.read"})
    assert server.permissions_for("write") == frozenset({"filesystem.write"})
