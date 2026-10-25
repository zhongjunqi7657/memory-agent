"""Environment bearer references and in-memory public-client OAuth PKCE grants."""

import asyncio
import base64
import hashlib
import math
import os
import secrets
import time
from urllib.parse import urlencode

import httpx

from app.mcp.http_security import http_client


class AuthenticationRequired(ValueError):
    def __init__(self):
        super().__init__("MCP 需要有效凭据或重新授权")


class OAuthGrants:
    """Secrets never leave process memory; reconnect does not replay operations."""

    def __init__(self):
        self._tokens = {}
        self._pending = {}
        self._locks = {}
        self._versions = {}

    def begin(self, server) -> str:
        config = server.auth.oauth
        if config is None:
            raise ValueError("连接未配置 OAuth")
        verifier = secrets.token_urlsafe(48)
        state = secrets.token_urlsafe(32)
        # Replace any previous pending attempt for the same connection.
        self._pending = {key: item for key, item in self._pending.items()
                         if item[0].id != server.id and item[2] > time.monotonic()}
        self._pending[state] = (server.model_copy(deep=True), verifier,
                                time.monotonic() + config.authorization_timeout_seconds)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        return config.authorization_url + "?" + urlencode({
            "response_type": "code", "client_id": config.client_id,
            "redirect_uri": config.redirect_uri, "scope": config.scope,
            "state": state, "code_challenge": challenge, "code_challenge_method": "S256",
            "resource": server.url,
        })

    async def complete(self, state: str, code: str, callback_uri: str, current_servers) -> str:
        pending = self._pending.pop(state, None)  # Single-use, including failed exchanges.
        if pending is None or pending[2] <= time.monotonic() or not code:
            raise AuthenticationRequired()
        server, verifier, _ = pending
        current = next((item for item in current_servers if item.id == server.id), None)
        if current != server or callback_uri != server.auth.oauth.redirect_uri:
            raise AuthenticationRequired()
        config = server.auth.oauth
        version = self._versions.get(server.id, 0)
        token = await self._exchange(config.token_url, {
            "grant_type": "authorization_code", "client_id": config.client_id,
            "code": code, "code_verifier": verifier, "redirect_uri": config.redirect_uri,
            "resource": server.url,
        }, server.command_timeout_seconds, config.default_token_ttl_seconds)
        if self._versions.get(server.id, 0) != version:
            raise AuthenticationRequired()
        self._tokens[server.id] = token
        return server.id

    @staticmethod
    async def _exchange(url, data, timeout, default_ttl):
        try:
            async with http_client(timeout=timeout) as client:
                response = await client.post(url, data=data)
                response.raise_for_status()
                payload = response.json()
            if (not isinstance(payload, dict) or str(payload.get("token_type", "")).lower() != "bearer"
                    or not isinstance(payload.get("access_token"), str) or not payload["access_token"]
                    or "\r" in payload["access_token"] or "\n" in payload["access_token"]
                    or (payload.get("refresh_token") is not None and not isinstance(payload["refresh_token"], str))):
                raise AuthenticationRequired()
            expires = float(payload.get("expires_in", default_ttl))
            if not math.isfinite(expires) or expires <= 0:
                raise AuthenticationRequired()
            return {"access_token": payload["access_token"], "refresh_token": payload.get("refresh_token"),
                    "expires_at": time.monotonic() + expires}
        except (httpx.HTTPError, ValueError, TypeError, KeyError):
            raise AuthenticationRequired() from None

    async def token(self, server):
        if server.auth.type == "none":
            return None
        if server.auth.type == "bearer":
            token = os.environ.get(server.auth.token_env)
            if not token or "\n" in token or "\r" in token:
                raise AuthenticationRequired()
            return token
        async with self._locks.setdefault(server.id, asyncio.Lock()):
            token = self._tokens.get(server.id)
            if token is None:
                raise AuthenticationRequired()
            if token["expires_at"] <= time.monotonic():
                if not token.get("refresh_token"):
                    self.revoke(server.id)
                    raise AuthenticationRequired()
                config = server.auth.oauth
                try:
                    renewed = await self._exchange(config.token_url, {
                        "grant_type": "refresh_token", "client_id": config.client_id,
                        "refresh_token": token["refresh_token"], "resource": server.url,
                    }, server.command_timeout_seconds, config.default_token_ttl_seconds)
                except AuthenticationRequired:
                    if self._tokens.get(server.id) is token:
                        self.revoke(server.id)
                    raise
                if self._tokens.get(server.id) is not token:
                    raise AuthenticationRequired()
                renewed["refresh_token"] = renewed.get("refresh_token") or token["refresh_token"]
                self._tokens[server.id] = token = renewed
            return token["access_token"]

    def revoke(self, server_id):
        self._versions[server_id] = self._versions.get(server_id, 0) + 1
        self._tokens.pop(server_id, None)
        self._pending = {key: item for key, item in self._pending.items() if item[0].id != server_id}


class MCPHTTPAuth(httpx.Auth):
    def __init__(self, server, grants):
        self.server, self.grants = server, grants

    async def async_auth_flow(self, request):
        # Session-provided URLs must never expand the configured network boundary.
        expected = httpx.URL(self.server.url)
        if (request.url.scheme, request.url.host, request.url.port) != (expected.scheme, expected.host, expected.port):
            raise AuthenticationRequired()
        token = await self.grants.token(self.server)
        if token:
            request.headers["Authorization"] = "Bearer " + token
        response = yield request
        if response.status_code in {401, 403}:
            self.grants.revoke(self.server.id)
            raise AuthenticationRequired()  # Never retry a tool request after auth failure.
