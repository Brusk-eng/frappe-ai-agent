"""Unit tests for multi-tenant site resolution, middleware, and session auth."""

import time
from typing import Any, cast
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.requests import Request

from ai_agent.config import Settings
from ai_agent.integrations.frappe_routes import SiteResolver, UnknownSite
from ai_agent.integrations.mcp import build_mcp_client_for_sid
from ai_agent.middleware.sid import UserContext, extract_user_context, require_sid
from ai_agent.middleware.site import (
    SiteMiddleware,
    current,
    current_bench_url,
    frappe_header,
)


def test_site_resolver_default_fallback():
    resolver = SiteResolver(default_url="http://bench:8000")
    assert resolver.resolve(None) == "http://bench:8000"
    assert resolver.resolve("") == "http://bench:8000"
    assert resolver.resolve("site1.local") == "http://bench:8000"


def test_site_resolver_template():
    resolver = SiteResolver(
        default_url="http://bench:8000",
        url_template="http://bench-{site}.frappe.svc.cluster.local:8000",
    )
    assert resolver.resolve("tenant-a") == "http://bench-tenant-a.frappe.svc.cluster.local:8000"
    assert resolver.resolve(None) == "http://bench:8000"


def test_site_resolver_routes_file_hot_reload(tmp_path):
    routes_file = tmp_path / "routes.yaml"
    routes_file.write_text(
        "routes:\n  tenant-1.local: http://bench-01:8000\n  tenant-2.local: http://bench-02:8000\n"
    )

    resolver = SiteResolver(
        default_url="http://default-bench:8000",
        routes_file=str(routes_file),
        reload_interval_s=0.01,
    )
    assert resolver.resolve("tenant-1.local") == "http://bench-01:8000"
    assert resolver.resolve("tenant-2.local") == "http://bench-02:8000"

    with pytest.raises(UnknownSite):
        resolver.resolve("unknown.local")

    # Update file and ensure hot-reload picks it up
    time.sleep(0.02)
    routes_file.write_text(
        "routes:\n  tenant-1.local: http://bench-01:8000\n  unknown.local: http://bench-new:8000\n"
    )
    assert resolver.resolve("unknown.local") == "http://bench-new:8000"


def test_site_middleware_sets_context_and_cleans_up():
    app = FastAPI()
    app.add_middleware(SiteMiddleware)

    captured_site = []

    @app.get("/test")
    def handler(request: Request):
        captured_site.append(current())
        return {"site": current(), "header": frappe_header()}

    client = TestClient(app)
    resp = client.get("/test", headers={"X-Frappe-Site-Name": "tenant-x.local"})
    assert resp.status_code == 200
    assert resp.json()["site"] == "tenant-x.local"
    assert resp.json()["header"] == {"X-Frappe-Site-Name": "tenant-x.local"}
    assert captured_site == ["tenant-x.local"]

    # Outside the request, context is reset
    assert current() is None
    assert frappe_header() == {}


def test_extract_user_context_with_site():
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/api/v1/chat",
        "headers": [
            (b"cookie", b"sid=sess123"),
            (b"x-frappe-site-name", b"tenant-alpha.local"),
        ],
        "query_string": b"",
    }
    req = Request(scope=scope)
    ctx = extract_user_context(req)
    assert ctx is not None
    assert ctx.sid == "sess123"
    assert ctx.site == "tenant-alpha.local"


@pytest.mark.asyncio
async def test_require_sid_resolves_tenant_url():
    settings = Settings(_env_file=None, frappe_url="http://bench-default:8000")  # pyright: ignore[reportCallIssue]
    resolver = SiteResolver(
        default_url="http://bench-default:8000",
        url_template="http://{site}.local:8000",
    )

    app = FastAPI()
    app.state.settings = settings
    app.state.site_resolver = resolver

    scope = {
        "type": "http",
        "method": "POST",
        "path": "/api/v1/chat",
        "headers": [
            (b"cookie", b"sid=valid-sid"),
            (b"x-frappe-site-name", b"site-xyz"),
        ],
        "query_string": b"",
        "app": app,
    }
    req = Request(scope=scope)

    with patch(
        "ai_agent.middleware.sid.signed_in_user", new=AsyncMock(return_value="user@example.com")
    ) as mock_auth:
        user_ctx = await require_sid(req)
        assert user_ctx.sid == "valid-sid"
        assert user_ctx.site == "site-xyz"
        assert user_ctx.frappe_base_url == "http://site-xyz.local:8000"
        mock_auth.assert_called_once_with("http://site-xyz.local:8000", "valid-sid", "site-xyz")


def test_site_resolver_routes_file_requires_site(tmp_path):
    routes_file = tmp_path / "routes.yaml"
    routes_file.write_text("routes:\n  tenant-1.local: http://bench-01:8000\n")

    resolver = SiteResolver(
        default_url="http://bench-sentinel.invalid:8000",
        routes_file=str(routes_file),
    )
    with pytest.raises(UnknownSite, match="site name required when routes file configured"):
        resolver.resolve(None)

    with pytest.raises(UnknownSite, match="site name required when routes file configured"):
        resolver.resolve("")

    with pytest.raises(UnknownSite, match="site name required when routes file configured"):
        resolver.resolve("   ")


@pytest.mark.asyncio
async def test_require_sid_unknown_site_yields_400(tmp_path):
    routes_file = tmp_path / "routes.yaml"
    routes_file.write_text("routes:\n  tenant-1.local: http://bench-01:8000\n")

    settings = Settings(_env_file=None, frappe_url="http://bench-sentinel.invalid:8000")  # pyright: ignore[reportCallIssue]
    resolver = SiteResolver(
        default_url="http://bench-sentinel.invalid:8000",
        routes_file=str(routes_file),
    )

    app = FastAPI()
    app.state.settings = settings
    app.state.site_resolver = resolver

    scope = {
        "type": "http",
        "method": "POST",
        "path": "/api/v1/chat",
        "headers": [
            (b"cookie", b"sid=valid-sid"),
            (b"x-frappe-site-name", b"unknown.local"),
        ],
        "query_string": b"",
        "app": app,
    }
    req = Request(scope=scope)

    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc_info:
        await require_sid(req)
    assert exc_info.value.status_code == 400
    assert "unknown site: unknown.local" in exc_info.value.detail


@pytest.mark.asyncio
async def test_require_sid_missing_site_with_routes_configured_yields_400(tmp_path):
    routes_file = tmp_path / "routes.yaml"
    routes_file.write_text("routes:\n  tenant-1.local: http://bench-01:8000\n")

    settings = Settings(_env_file=None, frappe_url="http://bench-sentinel.invalid:8000")  # pyright: ignore[reportCallIssue]
    resolver = SiteResolver(
        default_url="http://bench-sentinel.invalid:8000",
        routes_file=str(routes_file),
    )

    app = FastAPI()
    app.state.settings = settings
    app.state.site_resolver = resolver

    # Request without X-Frappe-Site-Name header
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/api/v1/chat",
        "headers": [
            (b"cookie", b"sid=valid-sid"),
        ],
        "query_string": b"",
        "app": app,
    }
    req = Request(scope=scope)

    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc_info:
        await require_sid(req)
    assert exc_info.value.status_code == 400
    assert "site name required when routes file configured" in exc_info.value.detail


@pytest.mark.asyncio
async def test_chat_service_streamed_turn_binds_site_contextvar():
    from unittest.mock import MagicMock

    from ai_agent.services.chat import ChatService

    settings = Settings(_env_file=None, frappe_url="http://bench:8000")  # pyright: ignore[reportCallIssue]
    mock_llm = MagicMock()
    mock_history = AsyncMock()
    mock_history.chat_doctypes_exist = AsyncMock(return_value=True)
    mock_history.create_session = AsyncMock(return_value="sess-test")
    mock_history.list_messages = AsyncMock(return_value=[])
    mock_history.save_message = AsyncMock(return_value="msg-1")

    service = ChatService(settings=settings, llm=mock_llm, history=mock_history)

    captured_sites_during_stream = []

    user_ctx = UserContext(
        sid="test-sid", site="tenant-live.local", frappe_base_url="http://bench:8000"
    )

    with patch.object(service, "_load_tools", new=AsyncMock(return_value=([], None))):
        with patch("ai_agent.services.chat.run_agent_loop") as mock_loop:

            async def fake_loop(**kwargs):
                captured_sites_during_stream.append(current())
                yield {"type": "content", "text": "hello"}

            mock_loop.side_effect = fake_loop
            agen = service.handle_message(
                message="hi",
                session_id=None,
                context={},
                user_context=user_ctx,
            )
            async for _ in agen:
                pass

    # During stream execution, site was bound in contextvar
    assert captured_sites_during_stream == ["tenant-live.local"]
    # After stream finishes, contextvar was cleaned up
    assert current() is None


def test_site_resolver_bench_url_override(tmp_path):
    routes_file = tmp_path / "routes.yaml"
    routes_file.write_text("routes:\n  tenant-1.local: http://bench-01:8000\n")

    resolver = SiteResolver(
        default_url="http://bench-default:8000",
        routes_file=str(routes_file),
    )
    # When bench_url is provided, it overrides routes file mapping
    assert (
        resolver.resolve("tenant-1.local", bench_url="http://override-bench:8000")
        == "http://override-bench:8000"
    )
    # When bench_url is provided for an unknown site, it resolves without error
    assert (
        resolver.resolve("unknown.local", bench_url="http://dynamic-bench:8000")
        == "http://dynamic-bench:8000"
    )
    # When bench_url is invalid, ValueError is raised
    with pytest.raises(ValueError, match="URL must be an absolute http"):
        resolver.resolve("tenant-1.local", bench_url="not-a-valid-url")


def test_site_middleware_sets_bench_url_context_and_cleans_up():
    app = FastAPI()
    app.add_middleware(SiteMiddleware)

    captured_data = []

    @app.get("/test")
    def handler(request: Request):
        captured_data.append((current(), current_bench_url()))
        return {"site": current(), "bench_url": current_bench_url(), "headers": frappe_header()}

    client = TestClient(app)
    resp = client.get(
        "/test",
        headers={
            "X-Frappe-Site-Name": "tenant-y.local",
            "X-Frappe-Bench-URL": "http://bench-y:8000/",
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["site"] == "tenant-y.local"
    assert data["bench_url"] == "http://bench-y:8000/"
    assert data["headers"] == {
        "X-Frappe-Site-Name": "tenant-y.local",
        "X-Frappe-Bench-URL": "http://bench-y:8000/",
    }
    assert captured_data == [("tenant-y.local", "http://bench-y:8000/")]

    # Outside the request, both contextvars are reset
    assert current() is None
    assert current_bench_url() is None
    assert frappe_header() == {}


def test_extract_user_context_with_bench_url():
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/api/v1/chat",
        "headers": [
            (b"cookie", b"sid=sess456"),
            (b"x-frappe-site-name", b"tenant-beta.local"),
            (b"x-frappe-bench-url", b"http://bench-beta:8000"),
        ],
        "query_string": b"",
    }
    req = Request(scope=scope)
    ctx = extract_user_context(req)
    assert ctx is not None
    assert ctx.sid == "sess456"
    assert ctx.site == "tenant-beta.local"
    assert ctx.bench_url == "http://bench-beta:8000"


@pytest.mark.asyncio
async def test_require_sid_resolves_bench_url_header():
    settings = Settings(_env_file=None, frappe_url="http://bench-default:8000")  # pyright: ignore[reportCallIssue]
    resolver = SiteResolver(default_url="http://bench-default:8000")

    app = FastAPI()
    app.state.settings = settings
    app.state.site_resolver = resolver

    scope = {
        "type": "http",
        "method": "POST",
        "path": "/api/v1/chat",
        "headers": [
            (b"cookie", b"sid=valid-sid"),
            (b"x-frappe-site-name", b"site-dynamic"),
            (b"x-frappe-bench-url", b"http://dynamic-bench:8000/"),
        ],
        "query_string": b"",
        "app": app,
    }
    req = Request(scope=scope)

    with patch(
        "ai_agent.middleware.sid.signed_in_user", new=AsyncMock(return_value="user@dynamic.local")
    ) as mock_auth:
        user_ctx = await require_sid(req)
        assert user_ctx.sid == "valid-sid"
        assert user_ctx.site == "site-dynamic"
        assert user_ctx.bench_url == "http://dynamic-bench:8000/"
        assert user_ctx.frappe_base_url == "http://dynamic-bench:8000"
        mock_auth.assert_called_once_with("http://dynamic-bench:8000", "valid-sid", "site-dynamic")


@pytest.mark.asyncio
async def test_require_sid_invalid_bench_url_yields_400():
    from fastapi import HTTPException

    settings = Settings(_env_file=None, frappe_url="http://bench-default:8000")  # pyright: ignore[reportCallIssue]
    resolver = SiteResolver(default_url="http://bench-default:8000")

    app = FastAPI()
    app.state.settings = settings
    app.state.site_resolver = resolver

    scope = {
        "type": "http",
        "method": "POST",
        "path": "/api/v1/chat",
        "headers": [
            (b"cookie", b"sid=valid-sid"),
            (b"x-frappe-bench-url", b"not-a-valid-url"),
        ],
        "query_string": b"",
        "app": app,
    }
    req = Request(scope=scope)

    with pytest.raises(HTTPException) as exc_info:
        await require_sid(req)
    assert exc_info.value.status_code == 400


def test_build_mcp_client_forwards_bench_url():
    settings = Settings(_env_file=None, mcp_server_url="http://mcp:8080/mcp")  # pyright: ignore[reportCallIssue]
    client = build_mcp_client_for_sid(
        settings,
        sid="session-xyz",
        site_name="tenant.local",
        bench_url="http://bench-target:8000",
    )
    conn = cast(dict[str, Any], client.connections["frappe"])
    headers = conn.get("headers") or {}
    assert headers.get("Cookie") == "sid=session-xyz"
    assert headers.get("X-Frappe-Site-Name") == "tenant.local"
    assert headers.get("X-Frappe-Bench-URL") == "http://bench-target:8000"


@pytest.mark.asyncio
async def test_chat_service_streamed_turn_binds_bench_url_contextvar():
    from unittest.mock import MagicMock

    from ai_agent.services.chat import ChatService

    settings = Settings(_env_file=None, frappe_url="http://bench:8000")  # pyright: ignore[reportCallIssue]
    mock_llm = MagicMock()
    mock_history = AsyncMock()
    mock_history.chat_doctypes_exist = AsyncMock(return_value=True)
    mock_history.create_session = AsyncMock(return_value="sess-test")
    mock_history.list_messages = AsyncMock(return_value=[])
    mock_history.save_message = AsyncMock(return_value="msg-1")

    service = ChatService(settings=settings, llm=mock_llm, history=mock_history)

    captured_bench_urls = []
    captured_load_tools_kwargs = {}

    user_ctx = UserContext(
        sid="test-sid",
        site="tenant-live.local",
        bench_url="http://dynamic-bench:8000",
        frappe_base_url="http://dynamic-bench:8000",
    )

    async def fake_load_tools(**kwargs):
        captured_load_tools_kwargs.update(kwargs)
        return ([], None)

    with patch.object(service, "_load_tools", side_effect=fake_load_tools):
        with patch("ai_agent.services.chat.run_agent_loop") as mock_loop:

            async def fake_loop(**kwargs):
                captured_bench_urls.append(current_bench_url())
                yield {"type": "content", "text": "hello"}

            mock_loop.side_effect = fake_loop
            agen = service.handle_message(
                message="hi",
                session_id=None,
                context={},
                user_context=user_ctx,
            )
            async for _ in agen:
                pass

    assert captured_bench_urls == ["http://dynamic-bench:8000"]
    assert captured_load_tools_kwargs.get("bench_url") == "http://dynamic-bench:8000"
    assert current_bench_url() is None
