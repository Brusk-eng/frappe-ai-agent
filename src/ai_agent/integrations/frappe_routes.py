"""Multi-tenant site resolver: maps site name to Frappe base URL."""

from __future__ import annotations

import os
import time
from urllib.parse import urlparse

import structlog
import yaml

from ai_agent.middleware.site import UnknownSite

logger = structlog.get_logger(__name__)


def validate_url(url: str, context: str = "") -> str:
    """Validate that a URL is absolute http(s)."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        prefix = f"{context}: " if context else ""
        raise ValueError(f"{prefix}URL must be an absolute http(s) URL, got: {url!r}")
    return url.rstrip("/")


class SiteResolver:
    """Resolves tenant site names to Frappe bench base URLs."""

    def __init__(
        self,
        default_url: str,
        routes_file: str = "",
        url_template: str = "",
        reload_interval_s: float = 2.0,
    ) -> None:
        self._default_url = validate_url(default_url, "default_url")
        self._routes_file = routes_file
        self._url_template = url_template
        self._reload_interval_s = reload_interval_s
        self._routes: dict[str, str] = {}
        self._last_mtime: float = -1.0
        self._last_check: float = 0.0

        if self._url_template:
            if "{site}" not in self._url_template:
                raise ValueError("url_template must contain '{site}' placeholder")

        if self._routes_file:
            self._load_routes(force=True)

    def _load_routes(self, force: bool = False) -> None:
        if not self._routes_file:
            return
        now = time.monotonic()
        if not force and (now - self._last_check < self._reload_interval_s):
            return
        self._last_check = now

        try:
            mtime = os.path.getmtime(self._routes_file)
        except OSError as exc:
            if force:
                raise ValueError(f"routes_file cannot be accessed: {exc}") from exc
            return

        if not force and mtime == self._last_mtime:
            return

        try:
            with open(self._routes_file, encoding="utf-8") as f:
                data = yaml.safe_load(f)
            routes_data = (data or {}).get("routes") if isinstance(data, dict) else None
            if not isinstance(routes_data, dict):
                raise ValueError(f"routes_file must contain a 'routes' mapping, got {type(data)}")

            validated: dict[str, str] = {}
            for site, url in routes_data.items():
                if not isinstance(site, str) or not isinstance(url, str):
                    continue
                validated[site.strip()] = validate_url(url.strip(), f"route for {site}")

            self._routes = validated
            self._last_mtime = mtime
            logger.info("site_routes_loaded", count=len(self._routes), path=self._routes_file)
        except Exception as exc:
            if force:
                raise ValueError(f"failed to load routes from {self._routes_file}: {exc}") from exc
            logger.warning("site_routes_reload_failed", error=str(exc))

    def resolve(self, site: str | None) -> str:
        """Resolve a site name to its Frappe base URL."""
        if self._routes_file and (site is None or not site.strip()):
            raise UnknownSite("site name required when routes file configured")

        if site is None or not site.strip():
            return self._default_url

        site = site.strip()
        if self._routes_file:
            self._load_routes()
            if site in self._routes:
                return self._routes[site]
            raise UnknownSite(f"unknown site: {site}")

        if self._url_template:
            url = self._url_template.format(site=site)
            return validate_url(url, f"template result for {site}")

        return self._default_url
