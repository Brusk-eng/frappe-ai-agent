"""Web search and page reading over plain HTTP: no MCP server, no browser, no vendor SDK.

Four search backends, chosen by `AI_AGENT_WEB_SEARCH_PROVIDER`:

- `searxng`     a self-hosted SearXNG with its JSON format enabled; the vendor-neutral choice
- `duckduckgo`  the HTML endpoint, no key and no account; DuckDuckGo answers most server
                networks with a human check instead of results, which comes back as a
                tool error naming the fix
- `brave`       Brave Search API, with a key
- `tavily`      Tavily, with a key

`fetch` reads one public page as text. It resolves the host itself and refuses private,
loopback and link-local addresses on every hop of a redirect, so a model cannot be talked
into reading the agent's own network (the MCP server, Redis, a cloud metadata endpoint).
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import re
from dataclasses import dataclass
from html import unescape
from html.parser import HTMLParser
from typing import Any
from urllib.parse import parse_qs, urljoin, urlparse

import httpx

from ai_agent.config import WEB_SEARCH_PROVIDERS as PROVIDERS

_DUCKDUCKGO_URL = "https://html.duckduckgo.com/html/"
# The text of DuckDuckGo's bot challenge page, which comes back as a 202, not an error.
_CHALLENGE_MARK = "bots use DuckDuckGo too"
_BRAVE_URL = "https://api.search.brave.com/res/v1/web/search"
_TAVILY_URL = "https://api.tavily.com/search"

# What a page fetch presents itself as; some sites answer a bare client with an empty page.
_USER_AGENT = "Mozilla/5.0 (compatible; frappe-ai-agent/1.0; +https://github.com/vyogotech)"
_MAX_REDIRECTS = 5
_MAX_BODY_BYTES = 1_000_000
_TEXT_TYPES = ("text/html", "application/xhtml+xml", "text/plain", "application/json")


class WebError(RuntimeError):
    """What went wrong, in a line the model may read back to the user."""


@dataclass(frozen=True)
class SearchHit:
    title: str
    url: str
    snippet: str


class WebClient:
    """One provider's search and a page fetch, sharing a timeout."""

    def __init__(
        self,
        *,
        provider: str,
        base_url: str = "",
        api_key: str = "",
        timeout_s: float = 10.0,
        max_results: int = 5,
    ) -> None:
        if provider not in PROVIDERS:
            raise ValueError(f"unknown web search provider {provider!r}; one of {PROVIDERS}")
        self.provider = provider
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout_s = timeout_s
        self.max_results = max_results

    # ------------------------------------------------------------------ search

    async def search(self, query: str) -> list[SearchHit]:
        query = query.strip()
        if not query:
            raise WebError("The search query is empty.")
        async with httpx.AsyncClient(timeout=self.timeout_s, follow_redirects=True) as client:
            try:
                if self.provider == "duckduckgo":
                    hits = await self._duckduckgo(client, query)
                elif self.provider == "searxng":
                    hits = await self._searxng(client, query)
                elif self.provider == "brave":
                    hits = await self._brave(client, query)
                elif self.provider == "tavily":
                    hits = await self._tavily(client, query)
                else:
                    raise WebError("Web search is turned off.")
            except httpx.HTTPStatusError as exc:
                raise WebError(f"The search service answered {exc.response.status_code}.") from exc
            except httpx.HTTPError as exc:
                raise WebError(
                    f"The search service could not be reached: {type(exc).__name__}."
                ) from exc
        return hits[: self.max_results]

    async def _duckduckgo(self, client: httpx.AsyncClient, query: str) -> list[SearchHit]:
        response = await client.get(
            _DUCKDUCKGO_URL,
            params={"q": query, "kl": "wt-wt"},
            headers={"User-Agent": _USER_AGENT, "Accept": "text/html"},
        )
        response.raise_for_status()
        hits = _parse_duckduckgo(response.text)
        if not hits and _CHALLENGE_MARK in response.text:
            # 202 with a "select all squares containing a duck" page: this network is treated
            # as a bot. Not something to work around; something to configure around.
            raise WebError(
                "DuckDuckGo asked for a human check from this network. "
                "Configure a SearXNG, Brave or Tavily search provider."
            )
        return hits

    async def _searxng(self, client: httpx.AsyncClient, query: str) -> list[SearchHit]:
        if not self.base_url:
            raise WebError("No SearXNG URL is configured.")
        response = await client.get(
            f"{self.base_url}/search",
            params={"q": query, "format": "json", "safesearch": "1"},
            headers={"Accept": "application/json"},
        )
        response.raise_for_status()
        rows = _json(response).get("results") or []
        return [
            SearchHit(_text(r.get("title")), _text(r.get("url")), _text(r.get("content")))
            for r in rows
            if isinstance(r, dict) and r.get("url")
        ]

    async def _brave(self, client: httpx.AsyncClient, query: str) -> list[SearchHit]:
        if not self.api_key:
            raise WebError("No Brave Search API key is configured.")
        response = await client.get(
            _BRAVE_URL,
            params={"q": query, "count": self.max_results},
            headers={"Accept": "application/json", "X-Subscription-Token": self.api_key},
        )
        response.raise_for_status()
        rows = (_json(response).get("web") or {}).get("results") or []
        return [
            SearchHit(_text(r.get("title")), _text(r.get("url")), _text(r.get("description")))
            for r in rows
            if isinstance(r, dict) and r.get("url")
        ]

    async def _tavily(self, client: httpx.AsyncClient, query: str) -> list[SearchHit]:
        if not self.api_key:
            raise WebError("No Tavily API key is configured.")
        response = await client.post(
            _TAVILY_URL,
            json={"query": query, "max_results": self.max_results},
            headers={"Authorization": f"Bearer {self.api_key}"},
        )
        response.raise_for_status()
        rows = _json(response).get("results") or []
        return [
            SearchHit(_text(r.get("title")), _text(r.get("url")), _text(r.get("content")))
            for r in rows
            if isinstance(r, dict) and r.get("url")
        ]

    # ------------------------------------------------------------------- fetch

    async def fetch(self, url: str, *, max_chars: int, offset: int = 0) -> str:
        """The page at `url` as plain text, titled; `max_chars` of it from `offset`."""
        url = url.strip()
        async with httpx.AsyncClient(timeout=self.timeout_s, follow_redirects=False) as client:
            for _ in range(_MAX_REDIRECTS + 1):
                await _refuse_private(url)
                try:
                    response = await client.get(
                        url, headers={"User-Agent": _USER_AGENT, "Accept": "text/html, text/plain"}
                    )
                except httpx.HTTPError as exc:
                    raise WebError(f"The page could not be fetched: {type(exc).__name__}.") from exc
                if response.is_redirect and response.headers.get("location"):
                    url = urljoin(url, response.headers["location"])
                    continue
                break
            else:
                raise WebError("The page redirected too many times.")
        if response.status_code >= 400:
            raise WebError(f"The page answered {response.status_code}.")
        content_type = response.headers.get("content-type", "").split(";")[0].strip().lower()
        if content_type and not content_type.startswith(_TEXT_TYPES):
            raise WebError(f"The page is {content_type}, not text.")
        body = response.content[:_MAX_BODY_BYTES].decode(response.encoding or "utf-8", "replace")
        if content_type.startswith("text/html") or content_type == "application/xhtml+xml":
            title, text = html_to_text(body, url)
        else:
            title, text = "", body
        offset = max(0, offset)
        header = (f"Title: {title}\nURL: {url}\n" if title else f"URL: {url}\n") + (
            f"(from character {offset} of {len(text)})\n\n" if offset else "\n"
        )
        room = max(0, max_chars - len(header) - 80)
        if offset >= len(text) and text:
            return header + f"[nothing past character {len(text)}]"
        part = text[offset : offset + room]
        if offset + room < len(text):
            part += (
                f"\n[{len(text) - offset - room} more characters: call again with "
                f"offset={offset + room}]"
            )
        return header + part


# ------------------------------------------------------------------ helpers


def _text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value)).strip() if value is not None else ""


def _json(response: httpx.Response) -> dict[str, Any]:
    try:
        data = response.json()
    except ValueError as exc:
        raise WebError("The search service did not answer with JSON.") from exc
    return data if isinstance(data, dict) else {}


async def _refuse_private(url: str) -> None:
    """Refuse a URL that is not a public http(s) host: the agent's own network stays its own."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise WebError("Only http and https pages can be fetched.")
    host = parsed.hostname
    if not host:
        raise WebError("The URL names no host.")
    if host == "localhost" or host.endswith(".localhost") or host.endswith(".local"):
        raise WebError("That address is not public.")
    for address in await _resolve(host):
        if not _is_public(address):
            raise WebError("That address is not public.")


async def _resolve(host: str) -> list[str]:
    """Every address the host resolves to; a literal address resolves to itself."""
    try:
        ipaddress.ip_address(host)
        return [host]
    except ValueError:
        pass
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(host, None)
    except OSError as exc:
        raise WebError("The host could not be resolved.") from exc
    return sorted({str(info[4][0]) for info in infos})


def _is_public(address: str) -> bool:
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return not (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


# Dropped whole: code, styling, and the menus and footers that say nothing about the page.
_SKIP_TAGS = frozenset(
    {"script", "style", "noscript", "svg", "template", "head", "iframe", "nav", "aside", "footer"}
)
# A line break before and after: paragraphs, headings and containers.
_BLOCK_TAGS = frozenset(
    {
        "p",
        "div",
        "ul",
        "ol",
        "table",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "section",
        "article",
        "header",
        "pre",
        "blockquote",
    }
)
# A line break before only: one line each, so a list is not double-spaced.
_LINE_TAGS = frozenset({"br", "li", "tr", "dt", "dd", "hr"})
# A space before: cells of one row stay on its line.
_CELL_TAGS = frozenset({"td", "th"})


# A link target longer than this is tracking noise, not an address worth reading.
_HREF_MAX = 200


class _TextExtractor(HTMLParser):
    """The readable text of a page: block tags become line breaks, scripts and styles vanish.

    A link's target follows its text in parentheses, so a list of names on a page is also a
    list of addresses the model can read next: `Examplia Systems (https://…/examplia-systems)`.
    """

    def __init__(self, base_url: str = "") -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.title = ""
        self._base_url = base_url
        self._skip = 0
        self._in_title = False
        self._href = ""
        self._link_start = -1  # index into parts where the current link's text began

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _SKIP_TAGS:
            self._skip += 1
        elif tag == "title":
            self._in_title = True
        elif tag in _BLOCK_TAGS or tag in _LINE_TAGS:
            self.parts.append("\n")
        elif tag in _CELL_TAGS:
            self.parts.append(" ")
        elif tag == "a" and not self._skip:
            href = dict(attrs).get("href") or ""
            # an in-page anchor or a script link names no page worth following
            skip = not href or href.startswith(("#", "javascript:", "mailto:", "tel:"))
            self._href = "" if skip else urljoin(self._base_url, href)
            self._link_start = len(self.parts)

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP_TAGS:
            self._skip = max(0, self._skip - 1)
        elif tag == "title":
            self._in_title = False
        elif tag in _BLOCK_TAGS:
            self.parts.append("\n")
        elif tag == "a" and self._link_start >= 0:
            text = _text("".join(self.parts[self._link_start :]))
            href = self._href
            self._href, self._link_start = "", -1
            if (
                text
                and href.startswith(("http://", "https://"))
                and len(href) <= _HREF_MAX
                and href.rstrip("/") != text.rstrip("/")
            ):
                self.parts.append(f" ({href})")

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title += data
        elif not self._skip:
            self.parts.append(data)


def html_to_text(markup: str, base_url: str = "") -> tuple[str, str]:
    """(title, text) of an HTML document, with whitespace collapsed to what a reader needs."""
    extractor = _TextExtractor(base_url)
    extractor.feed(markup)
    extractor.close()
    raw = "".join(extractor.parts)
    lines = [re.sub(r"[ \t\r\f\v]+", " ", line).strip() for line in raw.split("\n")]
    text = re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()
    return _text(extractor.title), text


class _DuckDuckGoParser(HTMLParser):
    """The result links and snippets of DuckDuckGo's HTML endpoint."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.hits: list[SearchHit] = []
        self._href = ""
        self._title: list[str] | None = None
        self._snippet: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "a":
            return
        a = dict(attrs)
        classes = (a.get("class") or "").split()
        if "result__a" in classes:
            self._href = _unwrap_duckduckgo(a.get("href") or "")
            self._title = []
        elif "result__snippet" in classes:
            self._snippet = []

    def handle_endtag(self, tag: str) -> None:
        if tag != "a":
            return
        if self._title is not None:
            self._pending_title = _text("".join(self._title))
            self._title = None
        elif self._snippet is not None:
            snippet = _text("".join(self._snippet))
            self._snippet = None
            if self._href:
                self.hits.append(
                    SearchHit(getattr(self, "_pending_title", ""), self._href, snippet)
                )
                self._href = ""

    def handle_data(self, data: str) -> None:
        if self._title is not None:
            self._title.append(data)
        elif self._snippet is not None:
            self._snippet.append(data)


def _unwrap_duckduckgo(href: str) -> str:
    """The target of a DuckDuckGo redirect link (`//duckduckgo.com/l/?uddg=<url>`)."""
    if href.startswith("//"):
        href = "https:" + href
    parsed = urlparse(href)
    if parsed.netloc.endswith("duckduckgo.com") and parsed.path.startswith("/l/"):
        target = parse_qs(parsed.query).get("uddg", [""])[0]
        return unescape(target)
    return href


def _parse_duckduckgo(markup: str) -> list[SearchHit]:
    parser = _DuckDuckGoParser()
    parser.feed(markup)
    parser.close()
    return [h for h in parser.hits if h.url.startswith("http")]


def hits_as_json(hits: list[SearchHit]) -> str:
    """Search hits as the JSON array the loop reads sources from: `file`, `seq`, `content`."""
    rows = [
        {"seq": i, "file": h.url, "file_name": h.title, "content": h.snippet}
        for i, h in enumerate(hits, start=1)
    ]
    return json.dumps(rows, ensure_ascii=False)
