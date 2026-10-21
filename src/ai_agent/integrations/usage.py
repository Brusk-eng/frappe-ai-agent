"""An optional usage gate: may this site's user send another message?

Off unless `usage_check_url` is configured, so an agent run on its own behaves
exactly as before. An operator serving many sites from one agent can point it
at their own usage service; that service owns quotas and counting, the agent
only asks before answering.

It lives in the agent, not in the Frappe site, because the agent is the shared
service: a check inside a site can be bypassed by anyone able to call the
agent from that site, a check here cannot.
"""

from __future__ import annotations

import httpx
import structlog

from ai_agent.config import Settings

logger = structlog.get_logger(__name__)

NOT_AVAILABLE = "The AI Assistant is not available on this site right now."


async def refusal_reason(settings: Settings, site: str | None, user: str | None) -> str | None:
    """The usage service's reason to refuse this message, or None to go ahead.

    Unreachable or failing (5xx) service: go ahead and log, so its outage does
    not take every site's assistant down. A 4xx (the agent's token refused, or
    a site the service does not know) is a refusal: a gate that opens when it
    cannot identify the caller is not a gate.
    """
    url = settings.usage_check_url.strip()
    if not url:
        return None
    headers = {"Accept": "application/json"}
    if settings.usage_check_token:
        # Its own header, not Authorization, so a service that authenticates
        # users by Authorization cannot mistake it for a user's credential.
        headers["X-Usage-Token"] = settings.usage_check_token
    try:
        async with httpx.AsyncClient(timeout=settings.usage_check_timeout_s) as client:
            response = await client.post(url, json={"site": site, "user": user}, headers=headers)
    except httpx.HTTPError as exc:
        logger.warning("usage_check.unreachable", site=site, error=str(exc))
        return None
    if response.status_code >= 500:
        logger.warning("usage_check.failed", site=site, status=response.status_code)
        return None
    if response.status_code >= 400:
        logger.warning("usage_check.refused_caller", site=site, status=response.status_code)
        return NOT_AVAILABLE
    try:
        answer = (response.json() or {}).get("message") or {}
    except ValueError:
        logger.warning("usage_check.bad_answer", site=site)
        return None
    if answer.get("allowed"):
        return None
    return str(answer.get("reason") or NOT_AVAILABLE)
