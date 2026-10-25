"""Explicit MCP HTTP destinations; no redirects, proxy inheritance or URL secrets."""

from ipaddress import ip_address
from urllib.parse import urlsplit

import httpx


def loopback(host: str | None) -> bool:
    if host == "localhost":
        return True
    try:
        return ip_address(host or "").is_loopback
    except ValueError:
        return False


def validate_http_url(value: str) -> str:
    url = urlsplit(value)
    if (not url.hostname or url.username or url.password or url.query or url.fragment
            or url.scheme not in {"https", "http"} or "\\" in value or any(ord(char) < 33 for char in value)
            or (url.scheme == "http" and not loopback(url.hostname))):
        raise ValueError("MCP 地址须为无凭据和查询参数的 HTTPS 地址，HTTP 仅允许 loopback")
    try:
        _ = url.port
    except ValueError:
        raise ValueError("MCP 地址端口无效") from None
    return value


async def reject_redirect(response: httpx.Response) -> None:
    if response.is_redirect:
        raise ValueError("MCP HTTP 不允许重定向，请显式配置最终地址")


class ObservedHTTPClient(httpx.AsyncClient):
    def __init__(self, *args, on_error=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.on_error = on_error

    async def send(self, request, *args, **kwargs):
        try:
            response = await super().send(request, *args, **kwargs)
            # Stateless servers may legitimately decline the optional GET stream.
            if response.is_error and not (request.method == "GET" and response.status_code == 405):
                response.raise_for_status()
            return response
        except Exception as error:
            if self.on_error:
                self.on_error(error)
            raise


def http_client(*, timeout: float, auth=None, on_error=None) -> httpx.AsyncClient:
    return ObservedHTTPClient(timeout=timeout, auth=auth, follow_redirects=False, trust_env=False, on_error=on_error,
                             event_hooks={"response": [reject_redirect]})
