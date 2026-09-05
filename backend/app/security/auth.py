"""Optional shared-password protection for the public demonstration service."""

import secrets
from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from app.config.settings import Settings, get_settings

basic_auth = HTTPBasic(auto_error=False)


async def require_demo_auth(
    credentials: Annotated[HTTPBasicCredentials | None, Depends(basic_auth)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> None:
    """Require a shared password only when configured for the demo environment."""

    if not settings.demo_shared_password:
        return
    if credentials is None or not secrets.compare_digest(
        credentials.password, settings.demo_shared_password
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="需要演示环境密码",
            headers={"WWW-Authenticate": "Basic"},
        )
