"""Regressions for connection controls, persisted overrides and OAuth revocation."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI
from mcp.types import GetPromptResult, Prompt, PromptArgument

from app.api.mcp_connections import router
from app.mcp.auth import AuthenticationRequired, OAuthGrants
from app.mcp.catalog import content_adapters
from app.mcp.config import MCPConfig, MCPServerConfig
from app.mcp.manager import MCPConnectionManager


def oauth_server():
    return MCPServerConfig(id="web", transport="streamable_http", url="https://example.com/mcp",
        auth={"type": "oauth", "oauth": {"client_id": "client", "authorization_url": "https://example.com/auth",
            "token_url": "https://example.com/token", "redirect_uri": "http://127.0.0.1:5173/"}})


@pytest.mark.asyncio
async def test_same_origin_browser_get_uses_trusted_referer_without_allowing_originless_writes():
    app = FastAPI()
    app.include_router(router)
    app.state.mcp_manager = MCPConnectionManager(MCPConfig())
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, client=("127.0.0.1", 123)),
        base_url="http://127.0.0.1:8000") as client:
        browser = {"referer": "http://127.0.0.1:5173/", "sec-fetch-site": "same-origin"}
        assert (await client.get("/v1/mcp/connections", headers=browser)).status_code == 200
        for headers in ({}, {**browser, "referer": "https://attacker.example/"},
                        {**browser, "sec-fetch-site": "cross-site"}, {**browser, "origin": "http://["}):
            assert (await client.get("/v1/mcp/connections", headers=headers)).status_code == 403
        assert (await client.post("/v1/mcp/connections", headers=browser,
                                  json={"id": "a", "command": "python"})).status_code == 403


@pytest.mark.asyncio
async def test_saving_one_connection_does_not_pin_unedited_toml_connections(tmp_path):
    base = MCPConfig(servers=[{"id": "toml", "command": "python", "enabled": False}])
    path = tmp_path / "connections.json"
    manager = MCPConnectionManager(base, store_path=path)
    await manager.configure(MCPServerConfig(id="ui", command="python", enabled=False))
    updated = base.model_copy(deep=True)
    updated.servers[0].args = ["updated.py"]
    restored = MCPConnectionManager(updated, store_path=path)
    assert next(server for server in restored.config.servers if server.id == "toml").args == ["updated.py"]
    await restored.configure(MCPServerConfig(id="toml", command="python", args=["override.py"], enabled=False))
    restored = MCPConnectionManager(updated, store_path=path)
    assert {server.id: server.args for server in restored.config.servers} == {"ui": [], "toml": ["override.py"]}


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["refresh", "complete"])
async def test_revocation_during_token_exchange_never_restores_credentials(monkeypatch, operation):
    server = oauth_server()
    grants = OAuthGrants()
    started, release = asyncio.Event(), asyncio.Event()

    async def exchange(*args):
        started.set()
        await release.wait()
        return {"access_token": "new-token", "refresh_token": "refresh", "expires_at": float("inf")}

    monkeypatch.setattr(grants, "_exchange", exchange)
    if operation == "refresh":
        grants._tokens[server.id] = {"access_token": "old", "refresh_token": "refresh", "expires_at": 0}
        task = asyncio.create_task(grants.token(server))
    else:
        grants.begin(server)
        state = next(iter(grants._pending))
        task = asyncio.create_task(grants.complete(state, "code", server.auth.oauth.redirect_uri, [server]))
    await asyncio.wait_for(started.wait(), 1)
    grants.revoke(server.id)
    release.set()
    with pytest.raises(AuthenticationRequired):
        await task
    assert not grants._tokens


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [[], None, {"access_token": "token", "token_type": "Bearer", "refresh_token": ["invalid"]}])
async def test_malformed_oauth_token_responses_fail_with_normalized_auth_error(monkeypatch, payload):
    from app.mcp import auth

    transport = httpx.MockTransport(lambda request: httpx.Response(200, json=payload))
    monkeypatch.setattr(auth, "http_client", lambda **kwargs: httpx.AsyncClient(transport=transport))
    with pytest.raises(AuthenticationRequired):
        await OAuthGrants._exchange("https://example.com/token", {}, 1, 60)


@pytest.mark.asyncio
async def test_prompt_argument_names_cannot_collide_with_approval_metadata():
    server = MCPServerConfig(id="a", command="python", default_permissions={"capabilities": []})
    prompt = Prompt(name="custom", arguments=[PromptArgument(name="_mcp_target", required=True)])
    client = SimpleNamespace(capabilities={"prompts"}, list_prompts=AsyncMock(return_value=[prompt]),
        get_prompt=AsyncMock(return_value=GetPromptResult(messages=[])))
    adapter, = await content_adapters(server, client, 1)
    prepared = await adapter.prepare({"_mcp_target": "user-value"}, None)
    assert prepared["_mcp_target"] == "custom"
    await adapter.execute(prepared, None)
    client.get_prompt.assert_awaited_once_with("custom", {"_mcp_target": "user-value"})
