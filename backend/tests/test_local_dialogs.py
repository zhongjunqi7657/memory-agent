"""Local dialog origin checks and cancellation without opening test windows."""

import subprocess
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

import app.api.local_dialogs as dialogs
import app.workspace.directory_picker as picker
from app.config.settings import Settings, get_settings


@pytest.mark.asyncio
@pytest.mark.parametrize("selected", [None, "C:/项目/demo"])
async def test_directory_choice_and_cancel(monkeypatch, selected):
    app = FastAPI()
    app.include_router(dialogs.router)
    app.dependency_overrides[get_settings] = lambda: Settings(_env_file=None, demo_shared_password=None)
    monkeypatch.setattr(dialogs, "get_settings", lambda: Settings(_env_file=None))
    monkeypatch.setattr(dialogs, "choose_directory", lambda: selected)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://127.0.0.1:8000") as client:
        response = await client.post("/v1/local/directory-picker", headers={"Origin": "http://127.0.0.1:5173"})
    assert response.status_code == 200
    assert response.json() == {"directory": selected}


@pytest.mark.asyncio
@pytest.mark.parametrize("client_host,headers", [
    ("192.168.1.8", {"Origin": "http://127.0.0.1:5173"}),
    ("127.0.0.1", {}),
    ("127.0.0.1", {"Origin": "https://other.example"}),
    ("127.0.0.1", {"Origin": "http://127.0.0.1:5173", "Sec-Fetch-Site": "cross-site"}),
])
async def test_untrusted_request_never_opens_dialog(monkeypatch, client_host, headers):
    app = FastAPI()
    app.include_router(dialogs.router)
    app.dependency_overrides[get_settings] = lambda: Settings(_env_file=None, demo_shared_password=None)
    monkeypatch.setattr(dialogs, "get_settings", lambda: Settings(_env_file=None))

    def forbidden():
        pytest.fail("untrusted request opened a dialog")

    monkeypatch.setattr(dialogs, "choose_directory", forbidden)
    transport = ASGITransport(app=app, client=(client_host, 123))
    async with AsyncClient(transport=transport, base_url="http://127.0.0.1:8000") as client:
        response = await client.post("/v1/local/directory-picker", headers=headers)
    assert response.status_code == 403


def test_dialog_timeout_releases_lock_and_cancel_is_readable(monkeypatch):
    monkeypatch.setattr(picker.sys, "platform", "win32")
    monkeypatch.setattr(picker.subprocess, "CREATE_NO_WINDOW", 0x08000000, raising=False)

    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired("picker", 180)

    monkeypatch.setattr(picker.subprocess, "run", timeout)
    with pytest.raises(picker.DirectoryPickerError, match="超时"):
        picker.choose_directory()
    monkeypatch.setattr(picker.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout="null"))
    assert picker.choose_directory() is None


def test_second_dialog_is_rejected():
    if picker.sys.platform != "win32":
        with pytest.raises(picker.DirectoryPickerError, match="手动填写"):
            picker.choose_directory()
        return
    with picker._dialog_lock, pytest.raises(picker.DirectoryPickerError, match="已有"):
        picker.choose_directory()
