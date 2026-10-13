"""Site name context variable and middleware for multi-tenancy.

SiteMiddleware binds the request-scoped site name context variable and structlog
contextvars for standard HTTP requests (such as /health, /config, and initial dispatch).

Note: Starlette's BaseHTTPMiddleware.dispatch returns once a response is produced, which
for streaming endpoints (like /api/v1/chat's StreamingResponse) happens before the SSE
generator finishes. Therefore, ChatService.handle_message explicitly binds the site
contextvar and structlog contextvars for the full duration of streamed turns.
"""

from __future__ import annotations

from contextvars import ContextVar

import structlog
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

_FRAPPE_SITE_HEADER = "X-Frappe-Site-Name"
_FRAPPE_BENCH_URL_HEADER = "X-Frappe-Bench-URL"
_site_context: ContextVar[str | None] = ContextVar("site", default=None)
_bench_url_context: ContextVar[str | None] = ContextVar("bench_url", default=None)


class UnknownSite(Exception):
    """Raised when a site name is not recognized or not mapped to any bench."""


def current() -> str | None:
    """The site name bound for this request, or None outside one."""
    return _site_context.get()


def set_current(site: str | None) -> None:
    """Set the current site name."""
    _site_context.set(site)


def current_bench_url() -> str | None:
    """The bench URL bound for this request, or None outside one."""
    return _bench_url_context.get()


def set_current_bench_url(bench_url: str | None) -> None:
    """Set the current bench URL."""
    _bench_url_context.set(bench_url)


def frappe_header() -> dict[str, str]:
    """The headers a call to Frappe carries, or nothing if neither site nor bench URL is set."""
    headers: dict[str, str] = {}
    site = current()
    if site:
        headers[_FRAPPE_SITE_HEADER] = site
    bench_url = current_bench_url()
    if bench_url:
        headers[_FRAPPE_BENCH_URL_HEADER] = bench_url
    return headers


class SiteMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        site_header = request.headers.get(_FRAPPE_SITE_HEADER)
        site = site_header.strip() if site_header and site_header.strip() else None
        _site_context.set(site)

        bench_header = request.headers.get(_FRAPPE_BENCH_URL_HEADER)
        bench_url = bench_header.strip() if bench_header and bench_header.strip() else None
        _bench_url_context.set(bench_url)

        bind_vars: dict[str, str] = {}
        unbind_vars: list[str] = []
        if site:
            bind_vars["site"] = site
        else:
            unbind_vars.append("site")
        if bench_url:
            bind_vars["bench_url"] = bench_url
        else:
            unbind_vars.append("bench_url")

        if bind_vars:
            structlog.contextvars.bind_contextvars(**bind_vars)
        if unbind_vars:
            structlog.contextvars.unbind_contextvars(*unbind_vars)

        try:
            return await call_next(request)
        finally:
            _site_context.set(None)
            _bench_url_context.set(None)
            structlog.contextvars.unbind_contextvars("site", "bench_url")
