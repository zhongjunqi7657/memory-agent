"""Local control boundary, independent of user keys and tool approvals."""

import secrets
from urllib.parse import urlsplit

from starlette.datastructures import Headers
from starlette.responses import JSONResponse

from app.config.settings import get_settings
from app.mcp.http_security import loopback


def validate_local_settings(settings, *, local_enabled=False):
    if settings.app_env == "demo":
        if local_enabled or not settings.demo_shared_password:
            raise ValueError("演示部署必须设置共享密码并关闭本地执行")
        return
    if not loopback(settings.app_host):
        raise ValueError("本地 API 只允许绑定 loopback")
    token = settings.local_api_token or ""
    if len(token) < 32 or not token.isascii() or any(char.isspace() for char in token):
        raise ValueError("请设置至少 32 位 ASCII 随机 LOCAL_API_TOKEN")


def trusted_origin(value, settings):
    try:
        parsed = urlsplit(value)
        if (parsed.scheme not in {"http", "https"} or not loopback(parsed.hostname)
                or parsed.username or parsed.password or parsed.path not in {"", "/"}
                or parsed.query or parsed.fragment):
            return False
        # Never trust an origin constructed from an arbitrary Host header.
        allowed = {item.strip().rstrip("/") for item in settings.app_cors_origins.split(",")}
        allowed.update({f"http://{host}:{settings.app_port}" for host in ("localhost", "127.0.0.1", "[::1]")})
        return value.rstrip("/") in allowed
    except ValueError:
        return False


class LocalAccessMiddleware:
    def __init__(self, app, settings=None):
        self.app = app
        self.settings = settings

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        settings = self.settings or get_settings()
        if settings.app_env == "demo":
            return await self.app(scope, receive, send)
        headers = Headers(scope=scope)
        try:
            host = urlsplit("http://" + headers.get("host", "")).hostname
        except ValueError:
            host = None
        peer = scope.get("client")
        origin = headers.get("origin")
        error, status, challenge = None, 403, {}
        if not peer or not loopback(peer[0]) or not loopback(host):
            error = "本地 API 仅允许本机受信任页面访问"
        elif scope["method"] in {"GET", "HEAD"} and (scope["path"] == "/" or scope["path"].startswith("/assets/")):
            # OAuth returns from another site. Only the public shell/assets may
            # load here; their subsequent control requests still need the token.
            return await self.app(scope, receive, send)
        elif (headers.get("sec-fetch-site") == "cross-site"
                or (origin is not None and not trusted_origin(origin, settings))):
            error = "本地 API 仅允许本机受信任页面访问"
        elif not settings.local_api_token:
            error, status = "本地访问认证未配置，请使用本地启动入口", 503
        elif scope["method"] == "OPTIONS" and origin and headers.get("access-control-request-method"):
            return await self.app(scope, receive, send)
        elif not secrets.compare_digest(headers.get("x-local-token", "").encode(), settings.local_api_token.encode()):
            error, status = "需要本机访问令牌", 401
            challenge = {"WWW-Authenticate": "LocalToken"}
        if error:
            return await JSONResponse({"detail": error}, status_code=status,
                                      headers={"Cache-Control": "no-store", **challenge})(scope, receive, send)

        async def no_store(message):
            if message["type"] == "http.response.start":
                message.setdefault("headers", []).append((b"cache-control", b"no-store"))
            await send(message)
        await self.app(scope, receive, no_store)
