import json

import httpx
import pytest
from fastapi import FastAPI

from app.api.mcp_connections import router
from app.mcp.config import MCPConfig
from app.mcp.manager import MCPConnectionManager


@pytest.fixture
def managed_app(tmp_path):
    app = FastAPI()
    app.include_router(router)
    app.state.mcp_manager = MCPConnectionManager(MCPConfig(), store_path=tmp_path / "connections.json")
    return app


@pytest.mark.asyncio
@pytest.mark.parametrize("peer,origin", [("8.8.8.8", "http://127.0.0.1:5173"),
    ("127.0.0.1", "https://attacker.example"), ("127.0.0.1", "")])
async def test_management_rejects_remote_peers_and_untrusted_origins(managed_app, peer, origin):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=managed_app, client=(peer, 123)), base_url="http://127.0.0.1:8000") as client:
        response = await client.post("/v1/mcp/connections", headers={"origin": origin}, json={"id": "a", "command": "python"})
    assert response.status_code == 403
    assert managed_app.state.mcp_manager.config.servers == []


@pytest.mark.asyncio
async def test_management_save_persist_enable_and_invalid_credentials_never_echoed(managed_app):
    manager = managed_app.state.mcp_manager
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=managed_app, client=("127.0.0.1", 123)),
                                base_url="http://127.0.0.1:8000", headers={"origin": "http://127.0.0.1:5173"}) as client:
        saved = await client.post("/v1/mcp/connections", json={"id": "a", "command": "python", "enabled": False,
            "default_permissions": {"capabilities": []}})
        assert saved.status_code == 200
        assert manager.status()[0]["status"] == "stopped"  # Saving does not execute a command.
        restored = MCPConnectionManager(MCPConfig(), store_path=manager.store.path)
        assert restored.config.servers == manager.config.servers
        assert (await client.post("/v1/mcp/connections/a/connect", json={})).status_code == 409
        assert (await client.post("/v1/mcp/connections/a/disconnect", json={})).status_code == 200
        assert (await client.post("/v1/mcp/connections/missing/connect", json={})).status_code == 404
        for body in [{"id": "b", "command": "python", "args": ["--token", "top-secret"]},
                     {"id": "b", "transport": "streamable_http", "url": "https://user:top-secret@example.com/mcp"}]:
            response = await client.post("/v1/mcp/connections", json=body)
            assert response.status_code == 400
            assert "top-secret" not in response.text
            assert "top-secret" not in manager.store.path.read_text(encoding="utf-8")
        callback = await client.post("/v1/mcp/connections/oauth/complete", json={"state": "bad", "code": "top-secret", "callback_uri": ""})
        assert callback.status_code == 400
        assert "top-secret" not in callback.text
        validation = await client.post("/v1/mcp/connections/oauth/complete", json={"state": "", "code": "top-secret"})
        assert "top-secret" not in validation.text


@pytest.mark.asyncio
async def test_oauth_redirect_must_match_local_origin_and_configuration_is_nonsecret(managed_app):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=managed_app, client=("127.0.0.1", 123)),
        base_url="http://127.0.0.1:8000", headers={"origin": "http://127.0.0.1:5173"}) as client:
        body = {"id": "web", "transport": "streamable_http", "url": "https://example.com/mcp", "auth": {
            "type": "oauth", "oauth": {"client_id": "client", "authorization_url": "https://id.example/authorize",
            "token_url": "https://id.example/token", "redirect_uri": "http://127.0.0.1:9999/"}}}
        assert (await client.post("/v1/mcp/connections", json=body)).status_code == 200
        assert (await client.post("/v1/mcp/connections/web/oauth/start", json={})).status_code == 409
        body["auth"]["oauth"]["redirect_uri"] = "http://127.0.0.1:5173/"
        await client.post("/v1/mcp/connections", json=body)
        response = await client.post("/v1/mcp/connections/web/oauth/start", json={})
        assert response.status_code == 200
        assert "code_challenge_method=S256" in response.json()["authorization_url"]
        assert (await client.post("/v1/mcp/connections/web/oauth/revoke", json={})).status_code == 200
        assert not managed_app.state.mcp_manager.grants._pending
        assert "code_challenge" not in json.dumps((await client.get("/v1/mcp/connections")).json())


@pytest.mark.asyncio
async def test_malformed_json_or_wrong_body_types_never_echo_sensitive_input(managed_app):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=managed_app, client=("127.0.0.1", 123)),
        base_url="http://127.0.0.1:8000", headers={"origin": "http://127.0.0.1:5173"}) as client:
        for path in ("", "/oauth/complete"):
            response = await client.post("/v1/mcp/connections" + path, json=["top-secret"])
            assert response.status_code == 400
            assert "top-secret" not in response.text
            response = await client.post("/v1/mcp/connections" + path, content='{code: "top-secret"}')
            assert response.status_code == 400
            assert "top-secret" not in response.text
