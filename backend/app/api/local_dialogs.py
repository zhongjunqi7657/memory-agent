"""Explicit local UI actions; the dialog does not bind or authorize a project."""

import asyncio
from ipaddress import ip_address
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Request

from app.config.settings import get_settings
from app.security.auth import require_demo_auth
from app.workspace.directory_picker import DirectoryPickerError, choose_directory

router = APIRouter(prefix="/v1/local", tags=["local"], dependencies=[Depends(require_demo_auth)])


def is_loopback(host: str | None) -> bool:
    if host == "localhost":
        return True
    try:
        return ip_address(host or "").is_loopback
    except ValueError:
        return False


@router.post("/directory-picker")
async def directory_picker(request: Request):
    settings = get_settings()
    origin = request.headers.get("origin", "")
    allowed = {value.strip().rstrip("/") for value in settings.app_cors_origins.split(",")}
    allowed.add(str(request.base_url).rstrip("/"))
    if (not request.client or not is_loopback(request.client.host)
            or origin not in allowed or not is_loopback(urlsplit(origin).hostname)
            or request.headers.get("sec-fetch-site") == "cross-site"):
        raise HTTPException(status_code=403, detail="文件夹选择仅支持本机项目页面")
    try:
        directory = await asyncio.to_thread(choose_directory)
    except DirectoryPickerError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return {"directory": directory}
