import httpx
import pytest
from fastapi import FastAPI

from app.config.settings import Settings
from app.security.local_access import LocalAccessMiddleware, validate_local_settings


def local_app(**overrides):
    settings = Settings(_env_file=None, app_env="local", local_api_token="test-local-token-12345678901234567890", **overrides)
    app = FastAPI()
    app.add_middleware(LocalAccessMiddleware, settings=settings)

    @app.api_route("/v1/control", methods=["GET", "POST"])
    def control():
        return {"ok": True}

    return app


@pytest.mark.parametrize("method", ["GET", "POST"])
async def test_every_control_request_requires_token(method):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=local_app(), client=("127.0.0.1", 1)), base_url="http://127.0.0.1:8000") as client:
        assert (await client.request(method, "/v1/control")).status_code == 401
        assert (await client.request(method, "/v1/control?token=test-local-token-12345678901234567890", auth=("demo", "password"))).status_code == 401
        response = await client.request(method, "/v1/control", headers={"X-Local-Token": "test-local-token-12345678901234567890"})
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize("headers,peer", [
    ({"origin": "https://evil.example"}, "127.0.0.1"),
    ({"host": "evil.example:8000"}, "127.0.0.1"),
    ({"origin": "null"}, "127.0.0.1"),
    ({"sec-fetch-site": "cross-site"}, "127.0.0.1"),
    ({}, "192.168.1.2"),
])
async def test_rebinding_csrf_and_remote_peers_are_rejected_even_with_token(headers, peer):
    headers = {"X-Local-Token": "test-local-token-12345678901234567890", **headers}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=local_app(), client=(peer, 1)), base_url="http://127.0.0.1:8000") as client:
        response = await client.post("/v1/control", headers=headers)
    assert response.status_code == 403
    assert "test-local-token" not in response.text


async def test_missing_token_configuration_fails_closed():
    app = FastAPI()
    app.add_middleware(LocalAccessMiddleware, settings=Settings(_env_file=None, app_env="local"))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, client=("127.0.0.1", 1)), base_url="http://127.0.0.1:8000") as client:
        assert (await client.get("/health")).status_code == 503


@pytest.mark.parametrize("host", ["0.0.0.0", "evil.example"])
def test_local_startup_rejects_public_bind(host):
    with pytest.raises(ValueError):
        validate_local_settings(Settings(_env_file=None, app_host=host, local_api_token="a" * 32))


def test_runtime_paths_are_independent_of_current_directory(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ValueError):
        Settings(_env_file=None, app_data_dir="relative/data")


@pytest.mark.parametrize("path", ["/health", "/config", "/docs", "/v1/projects", "/v1/chat", "/v1/runs/anything/cancel", "/v1/mcp/connections"])
async def test_real_application_rejects_before_route_or_database_access(path, monkeypatch):
    from app.main import app
    from app.security import local_access

    monkeypatch.setattr(local_access, "get_settings", lambda: Settings(_env_file=None, local_api_token="a" * 32))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, client=("127.0.0.1", 1)), base_url="http://127.0.0.1:8000") as client:
        assert (await client.get(path)).status_code == 401


def test_demo_mode_cannot_enable_local_execution_or_omit_password():
    with pytest.raises(ValueError):
        validate_local_settings(Settings(_env_file=None, app_env="demo"))
    with pytest.raises(ValueError):
        validate_local_settings(Settings(_env_file=None, app_env="demo", demo_shared_password="demo"), local_enabled=True)


async def test_cross_origin_frontend_can_read_local_auth_challenge(monkeypatch):
    from app.main import app
    from app.security import local_access

    monkeypatch.setattr(local_access, "get_settings", lambda: Settings(_env_file=None, local_api_token="a" * 32))
    origin = "http://127.0.0.1:5173"
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, client=("127.0.0.1", 1)), base_url="http://127.0.0.1:8000") as client:
        response = await client.get("/config", headers={"Origin": origin})
        assert response.status_code == 401
        assert response.headers.get("access-control-allow-origin") == origin
        exposed = response.headers.get("access-control-expose-headers", "").lower()
        assert "www-authenticate" in exposed
        assert response.headers["www-authenticate"] == "LocalToken"
        preflight = await client.options("/v1/chat", headers={"Origin": origin,
            "Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "X-Local-Token,Content-Type"})
        assert preflight.status_code == 200


async def test_oauth_return_navigation_can_load_static_shell_but_not_control_routes(monkeypatch):
    from app.main import app
    from app.security import local_access

    monkeypatch.setattr(local_access, "get_settings", lambda: Settings(_env_file=None, local_api_token="a" * 32))
    headers = {"Sec-Fetch-Site": "cross-site", "Sec-Fetch-Mode": "navigate", "Sec-Fetch-Dest": "document"}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, client=("127.0.0.1", 1)), base_url="http://127.0.0.1:8000") as client:
        response = await client.get("/?code=oauth-return&state=fixture-state", headers=headers)
        assert response.status_code == 200
        assert "oauth-return" not in response.text
        assert (await client.get("/config", headers=headers)).status_code == 403
        assert (await client.post("/", headers=headers)).status_code == 403
