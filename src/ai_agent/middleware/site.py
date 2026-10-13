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
_site_context: ContextVar[str | None] = ContextVar("site", default=None)


class UnknownSite(Exception):
    """Raised when a site name is not recognized or not mapped to any bench."""


def current() -> str | None:
    """The site name bound for this request, or None outside one."""
    return _site_context.get()


def set_current(site: str | None) -> None:
    """Set the current site name."""
    _site_context.set(site)


def frappe_header() -> dict[str, str]:
    """The header a call to Frappe carries, or nothing if no site is set."""
    site = current()
    return {_FRAPPE_SITE_HEADER: site} if site else {}


class SiteMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        site_header = request.headers.get(_FRAPPE_SITE_HEADER)
        site = site_header.strip() if site_header and site_header.strip() else None
        _site_context.set(site)
        if site:
            structlog.contextvars.bind_contextvars(site=site)
        else:
            structlog.contextvars.unbind_contextvars("site")
        try:
            return await call_next(request)
        finally:
            _site_context.set(None)
            structlog.contextvars.unbind_contextvars("site")
