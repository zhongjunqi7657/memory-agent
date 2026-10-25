from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from mcp.client.session import _default_elicitation_callback, _default_sampling_callback

from app.mcp.auth import AuthenticationRequired, OAuthGrants
from app.mcp.client import MCPClient, MCPClientError
from app.mcp.config import MCPServerConfig
from app.mcp.http_security import reject_redirect


@pytest.mark.asyncio
async def test_unnegotiated_capabilities_never_call_sdk_and_bidirectional_requests_rejected():
    client = MCPClient(MCPServerConfig(id="a", command="python"))
    assert await client.list_tools() == ()
    assert await client.list_resources() == ()
    assert await client.list_prompts() == ()
    with pytest.raises(MCPClientError, match="未协商"):
        await client.get_prompt("anything", {})
    with pytest.raises(MCPClientError, match="未协商"):
        await client.read_resource("https://attacker.example")
    assert (await _default_sampling_callback(None, None)).code == -32600
    assert (await _default_elicitation_callback(None, None)).code == -32600


@pytest.mark.asyncio
async def test_pagination_repeated_cursor_fails_instead_of_looping():
    client = MCPClient(MCPServerConfig(id="a", command="python"))
    client.capabilities = frozenset({"resources"})
    session = SimpleNamespace(list_resources=AsyncMock(return_value=SimpleNamespace(resources=[], nextCursor="same")))
    client._connected_session = lambda: session
    with pytest.raises(MCPClientError):
        await client.list_resources()
    assert session.list_resources.await_count == 2


@pytest.mark.asyncio
async def test_http_redirects_never_follow_even_same_host():
    response = httpx.Response(307, headers={"location": "http://127.0.0.1/private"}, request=httpx.Request("POST", "https://example.com/mcp"))
    with pytest.raises(ValueError):
        await reject_redirect(response)


@pytest.mark.asyncio
async def test_expired_state_and_changed_config_do_not_exchange_credentials():
    server = MCPServerConfig(id="a", transport="streamable_http", url="https://example.com/mcp", auth={"type": "oauth",
        "oauth": {"client_id": "client", "authorization_url": "https://example.com/auth", "token_url": "https://example.com/token",
                  "redirect_uri": "http://127.0.0.1:5173/"}})
    grants = OAuthGrants()
    grants.begin(server)
    state = next(iter(grants._pending))
    pending = grants._pending[state]
    grants._pending[state] = (pending[0], pending[1], 0)
    with pytest.raises(AuthenticationRequired):
        await grants.complete(state, "secret-code", server.auth.oauth.redirect_uri, [server])
    grants.begin(server)
    state = next(iter(grants._pending))
    changed = server.model_copy(update={"url": "https://other.example/mcp"})
    with pytest.raises(AuthenticationRequired):
        await grants.complete(state, "secret-code", server.auth.oauth.redirect_uri, [changed])
    assert not grants._tokens
