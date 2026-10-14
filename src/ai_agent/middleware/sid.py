# src/ai_agent/middleware/sid.py
"""sid cookie auth — single source of truth for who is calling the agent."""

from __future__ import annotations

from dataclasses import dataclass

import httpx
from fastapi import HTTPException
from starlette.requests import Request

from ai_agent.middleware.site import UnknownSite
from ai_agent.observability import request_id as correlation


@dataclass(frozen=True, slots=True)
class UserContext:
    """The caller's Frappe session; `sid` goes on every call made for this user."""

    sid: str
    site: str | None = None
    # the caller's X-Frappe-Bench-URL: a routing hint for SiteResolver, never a destination
    bench_url: str | None = None
    # where this user's Frappe actually is, as resolved from operator config
    frappe_base_url: str = ""

    def __post_init__(self) -> None:
        if not self.sid or not self.sid.strip():
            raise ValueError("UserContext.sid must be a non-empty, non-whitespace string")


def extract_user_context(request: Request) -> UserContext | None:
    """Return a UserContext if the request carries a non-empty sid cookie."""
    sid = request.cookies.get("sid")
    if not sid or not sid.strip():
        return None
    site_header = request.headers.get("X-Frappe-Site-Name")
    site = site_header.strip() if site_header and site_header.strip() else None
    bench_header = request.headers.get("X-Frappe-Bench-URL")
    bench_url = bench_header.strip() if bench_header and bench_header.strip() else None
    return UserContext(sid=sid, site=site, bench_url=bench_url)


async def signed_in_user(frappe_url: str, sid: str, site: str | None = None) -> str | None:
    """The user signed in under `sid`, or None if unknown; a fresh client, so no cookie is kept.

    Raises:
        httpx.HTTPError: Frappe cannot be asked.
    """
    headers = {
        "Cookie": f"sid={sid}",
        "Accept": "application/json",
        **correlation.frappe_header(),
    }
    if site:
        headers["X-Frappe-Site-Name"] = site
    async with httpx.AsyncClient(timeout=5.0) as client:
        response = await client.get(
            f"{frappe_url}/api/method/frappe.auth.get_logged_user",
            headers=headers,
        )
    if response.status_code in (401, 403):
        return None
    response.raise_for_status()
    user = response.json().get("message")
    return user if isinstance(user, str) and user and user != "Guest" else None


async def require_sid(request: Request) -> UserContext:
    """Auth as a dependency: on chat it precedes @limiter.limit, so a 401 spends no token."""
    user_context = extract_user_context(request)
    if user_context is None:
        raise HTTPException(status_code=401, detail="Missing sid cookie")

    resolver = getattr(request.app.state, "site_resolver", None)
    try:
        if resolver:
            frappe_url = resolver.resolve(user_context.site, bench_url=user_context.bench_url)
        else:
            frappe_url = request.app.state.settings.frappe_url
    except (UnknownSite, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    try:
        user = await signed_in_user(frappe_url, user_context.sid, user_context.site)
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=503, detail="Cannot check your session right now") from exc
    if user is None:
        raise HTTPException(status_code=401, detail="Not signed in")
    return UserContext(
        sid=user_context.sid,
        site=user_context.site,
        bench_url=user_context.bench_url,
        frappe_base_url=frappe_url,
    )
