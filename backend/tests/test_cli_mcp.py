import json
from unittest.mock import patch

import pytest

from app.cli.main import main
from app.cli.mcp import test_server as check_server
from app.config.business import BusinessConfig
from tests.test_mcp_stdio_integration import fixture_config, process_alive


def test_cli_lists_configured_servers(capsys):
    assert main(["mcp", "list"]) == 0
    assert "servers" in json.loads(capsys.readouterr().out)


def test_cli_status_queries_api_only(capsys):
    with patch("app.cli.main.server_status", return_value={"servers": [{"id": "a", "status": "ready"}]}) as status:
        assert main(["mcp", "status", "--api-url", "http://localhost:9000"]) == 0
    status.assert_called_once_with("http://localhost:9000")
    assert json.loads(capsys.readouterr().out)["servers"][0]["status"] == "ready"


@pytest.mark.asyncio
async def test_cli_handshake_only_reuses_manager_and_reaps_process(tmp_path, monkeypatch):
    marker = tmp_path / "pid.txt"
    business = BusinessConfig(mcp=fixture_config(marker))

    async def forbidden(*args, **kwargs):
        pytest.fail("mcp test must not call tools/call")

    monkeypatch.setattr("app.mcp.client.MCPClient.call_tool", forbidden)
    result, exit_code = await check_server(business, "fixture")
    assert exit_code == 0
    assert result["server"]["tool_count"] == 5
    assert not process_alive(int(marker.read_text()))
