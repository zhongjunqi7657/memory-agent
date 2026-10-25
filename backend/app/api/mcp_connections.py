"""Local, explicit connection controls; invocation stays in the governed run API."""

from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.config.settings import get_settings
from app.mcp.config import MCPServerConfig
from app.mcp.http_security import loopback
from app.security.auth import require_demo_auth


def local_control(request: Request):
    settings = get_settings()
    origin = request.headers.get("origin", "")
    # Same-origin browser GETs omit Origin. Accept their browser-supplied
    # Referer only for reads; mutations still require an explicit Origin.
    if not origin and request.method == "GET" and request.headers.get("sec-fetch-site") == "same-origin":
        try:
            referer = urlsplit(request.headers.get("referer", ""))
            if referer.scheme in {"http", "https"} and not referer.username and not referer.password:
                origin = f"{referer.scheme}://{referer.netloc}"
        except ValueError:
            pass
    allowed = {value.strip().rstrip("/") for value in settings.app_cors_origins.split(",")}
    allowed.add(str(request.base_url).rstrip("/"))
    if (not request.client or not loopback(request.client.host) or origin not in allowed
            or request.headers.get("sec-fetch-site") == "cross-site"):
        raise HTTPException(403, "MCP 连接管理仅支持本机受信任页面")
    if not loopback(urlsplit(origin).hostname):
        raise HTTPException(403, "MCP 连接管理仅支持本机受信任页面")


router = APIRouter(prefix="/v1/mcp/connections", tags=["mcp"],
                   dependencies=[Depends(require_demo_auth), Depends(local_control)])


def manager_for(request):
    manager = getattr(request.app.state, "mcp_manager", None)
    if manager is None:
        raise HTTPException(503, "MCP 管理器未就绪")
    return manager


def server_for(manager, server_id):
    server = next((item for item in manager.config.servers if item.id == server_id), None)
    if server is None:
        raise HTTPException(404, "连接不存在")
    return server


@router.get("")
async def connections(request: Request):
    manager = manager_for(request)
    return {"servers": manager.connections(), "enabled": manager.config.enabled,
            "allowed_commands": manager.config.allowed_commands}


@router.post("")
async def configure(request: Request):
    # Do not echo Pydantic input values: pasted credentials must not enter errors/logs.
    try:
        server = MCPServerConfig.model_validate(await request.json())
        await manager_for(request).configure(server)
    except (ValueError, ValidationError):
        raise HTTPException(400, "连接配置无效：检查传输、地址、命令白名单、凭据引用及权限声明") from None
    except OSError:
        raise HTTPException(503, "连接记录保存失败") from None
    return {"saved": True, "id": server.id}


@router.post("/{server_id}/connect")
async def connect(server_id: str, request: Request):
    manager = manager_for(request)
    server_for(manager, server_id)
    try:
        await manager.reconnect(server_id)
    except ValueError:
        raise HTTPException(409, "连接已禁用或配置无效") from None
    return {"servers": manager.status()}


@router.post("/{server_id}/disconnect")
async def disconnect(server_id: str, request: Request):
    manager = manager_for(request)
    server_for(manager, server_id)
    await manager.stop(server_id)
    return {"servers": manager.status()}


@router.post("/{server_id}/oauth/start")
async def oauth_start(server_id: str, request: Request):
    manager = manager_for(request)
    server = server_for(manager, server_id)
    if not server.enabled or not manager.config.enabled or server.auth.type != "oauth":
        raise HTTPException(409, "连接未启用 OAuth")
    # Callback must match the trusted local frontend requesting authorization.
    if server.auth.oauth.redirect_uri.rstrip("/") != request.headers["origin"].rstrip("/"):
        raise HTTPException(409, "OAuth 回调须配置为当前本机前端根地址")
    return {"authorization_url": manager.grants.begin(server)}


class OAuthCallback(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    state: str = Field(min_length=1, max_length=256)
    code: str = Field(min_length=1, max_length=4096)
    callback_uri: str = Field(max_length=2048)


@router.post("/oauth/complete")
async def oauth_complete(request: Request):
    manager = manager_for(request)
    try:
        payload = OAuthCallback.model_validate(await request.json())
        server_id = await manager.complete_authorization(payload.state, payload.code, payload.callback_uri)
    except ValueError:
        raise HTTPException(400, "授权已失效、已使用或凭据交换失败，请重新授权") from None
    return {"id": server_id, "servers": manager.status()}


@router.post("/{server_id}/oauth/revoke")
async def oauth_revoke(server_id: str, request: Request):
    manager = manager_for(request)
    server_for(manager, server_id)
    await manager.stop(server_id, revoke=True)
    return {"revoked": True}
