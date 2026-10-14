"""The agent's own web tools: each provider's wire format, the page reader, and its refusals."""

from __future__ import annotations

import json
from typing import cast
from unittest.mock import patch

import httpx
import pytest
import respx

from ai_agent.agent.tool_registry import ToolRegistry
from ai_agent.agent.web_tools import WEB_FETCH_TOOL, WEB_SEARCH_TOOL, build_web_tools
from ai_agent.config import Settings
from ai_agent.integrations.web import WebClient, WebError, html_to_text

_DDG_PAGE = """
<html><body>
<div class="result">
  <a rel="nofollow" class="result__a"
     href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fgst&amp;rut=abc">
     GST <b>rates</b> 2026</a>
  <a class="result__snippet"
     href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fgst">
     The current <b>GST</b> rate is 18%.</a>
</div>
<div class="result">
  <a class="result__a" href="https://docs.example.org/page">Docs page</a>
  <a class="result__snippet" href="https://docs.example.org/page">A snippet.</a>
</div>
</body></html>
"""


def _settings(**overrides) -> Settings:
    return Settings(_env_file=None, **overrides)  # pyright: ignore[reportCallIssue]


class TestProviders:
    @respx.mock
    async def test_duckduckgo_parses_the_html_endpoint_and_unwraps_redirects(self):
        respx.get("https://html.duckduckgo.com/html/").mock(
            return_value=httpx.Response(200, text=_DDG_PAGE)
        )
        hits = await WebClient(provider="duckduckgo").search("gst rates")
        assert [h.url for h in hits] == ["https://example.com/gst", "https://docs.example.org/page"]
        assert hits[0].title == "GST rates 2026"
        assert hits[0].snippet == "The current GST rate is 18%."

    @respx.mock
    async def test_searxng_reads_the_json_format(self):
        route = respx.get("http://searx.test/search").mock(
            return_value=httpx.Response(
                200,
                json={"results": [{"title": "T", "url": "https://a.test/", "content": "C"}]},
            )
        )
        hits = await WebClient(provider="searxng", base_url="http://searx.test/").search("q")
        assert hits == [hits[0]] and hits[0].url == "https://a.test/"
        assert route.calls.last.request.url.params["format"] == "json"

    @respx.mock
    async def test_brave_sends_the_key_as_a_header(self):
        route = respx.get("https://api.search.brave.com/res/v1/web/search").mock(
            return_value=httpx.Response(
                200,
                json={
                    "web": {
                        "results": [{"title": "T", "url": "https://b.test/", "description": "D"}]
                    }
                },
            )
        )
        hits = await WebClient(provider="brave", api_key="k").search("q")
        assert hits[0].snippet == "D"
        assert route.calls.last.request.headers["X-Subscription-Token"] == "k"

    @respx.mock
    async def test_tavily_posts_the_query(self):
        route = respx.post("https://api.tavily.com/search").mock(
            return_value=httpx.Response(
                200, json={"results": [{"title": "T", "url": "https://t.test/", "content": "C"}]}
            )
        )
        hits = await WebClient(provider="tavily", api_key="k").search("q")
        assert hits[0].url == "https://t.test/"
        assert json.loads(route.calls.last.request.content)["query"] == "q"

    @respx.mock
    async def test_duckduckgo_human_check_is_named_not_hidden(self):
        # The challenge page is a 202 with no results: without this, "no results" would be
        # the model's answer to every question on a network DuckDuckGo distrusts.
        respx.get("https://html.duckduckgo.com/html/").mock(
            return_value=httpx.Response(
                202, text="<html><body>Unfortunately, bots use DuckDuckGo too.</body></html>"
            )
        )
        with pytest.raises(WebError, match="human check"):
            await WebClient(provider="duckduckgo").search("q")

    @respx.mock
    async def test_a_provider_error_is_one_plain_line(self):
        respx.get("https://html.duckduckgo.com/html/").mock(return_value=httpx.Response(429))
        with pytest.raises(WebError, match="answered 429"):
            await WebClient(provider="duckduckgo").search("q")

    async def test_results_are_capped(self):
        rows = [{"title": str(i), "url": f"https://x.test/{i}", "content": ""} for i in range(9)]
        with respx.mock:
            respx.get("http://searx.test/search").mock(
                return_value=httpx.Response(200, json={"results": rows})
            )
            hits = await WebClient(
                provider="searxng", base_url="http://searx.test", max_results=3
            ).search("q")
        assert len(hits) == 3


class TestFetch:
    @respx.mock
    async def test_html_becomes_titled_text_without_scripts(self):
        respx.get("https://example.com/a").mock(
            return_value=httpx.Response(
                200,
                headers={"content-type": "text/html; charset=utf-8"},
                text="<html><head><title>A page</title><style>p{}</style></head>"
                "<body><script>alert(1)</script><h1>Hello</h1>"
                "<p>World &amp; more</p></body></html>",
            )
        )
        with patch("ai_agent.integrations.web._resolve", return_value=["93.184.216.34"]):
            text = await WebClient(provider="duckduckgo").fetch(
                "https://example.com/a", max_chars=500
            )
        assert text.startswith("Title: A page\nURL: https://example.com/a\n\n")
        assert "Hello\n" in text and "World & more" in text
        assert "alert" not in text and "p{}" not in text

    @respx.mock
    async def test_long_pages_are_cut_with_a_marker(self):
        respx.get("https://example.com/long").mock(
            return_value=httpx.Response(
                200, headers={"content-type": "text/plain"}, text="x" * 10_000
            )
        )
        with patch("ai_agent.integrations.web._resolve", return_value=["93.184.216.34"]):
            text = await WebClient(provider="duckduckgo").fetch(
                "https://example.com/long", max_chars=300
            )
        assert "more characters: call again with offset=" in text
        assert len(text) < 400

    @respx.mock
    async def test_a_long_page_is_read_in_parts_by_offset(self):
        respx.get("https://example.com/list").mock(
            return_value=httpx.Response(
                200,
                headers={"content-type": "text/plain"},
                text="".join(f"row{i:04d}\n" for i in range(400)),
            )
        )
        client = WebClient(provider="duckduckgo")
        with patch("ai_agent.integrations.web._resolve", return_value=["93.184.216.34"]):
            first = await client.fetch("https://example.com/list", max_chars=600)
            assert "row0000" in first and "more characters: call again with offset=" in first
            offset = int(first.rsplit("offset=", 1)[1].rstrip("]"))
            second = await client.fetch("https://example.com/list", max_chars=600, offset=offset)
            assert f"(from character {offset} of" in second
            assert "row0000" not in second
            past = await client.fetch("https://example.com/list", max_chars=600, offset=10**6)
            assert "[nothing past character" in past

    @pytest.mark.parametrize(
        "url",
        [
            "http://127.0.0.1:8080/mcp",
            "http://10.0.0.5/",
            "http://192.168.1.1/",
            "http://169.254.169.254/latest/meta-data/",
            "http://[::1]/",
            "http://localhost:8000/",
            "http://redis.localhost/",
            "ftp://example.com/",
            "file:///etc/passwd",
        ],
    )
    async def test_private_and_non_http_addresses_are_refused(self, url: str):
        with pytest.raises(WebError):
            await WebClient(provider="duckduckgo").fetch(url, max_chars=500)

    async def test_a_public_name_that_resolves_privately_is_refused(self):
        with (
            patch("ai_agent.integrations.web._resolve", return_value=["10.1.2.3"]),
            pytest.raises(WebError, match="not public"),
        ):
            await WebClient(provider="duckduckgo").fetch("https://evil.example/", max_chars=500)

    @respx.mock
    async def test_a_redirect_to_a_private_address_is_refused(self):
        respx.get("https://example.com/r").mock(
            return_value=httpx.Response(302, headers={"location": "http://169.254.169.254/"})
        )

        async def _dns(host: str) -> list[str]:
            return ["93.184.216.34"] if host == "example.com" else [host]

        with (
            patch("ai_agent.integrations.web._resolve", side_effect=_dns),
            pytest.raises(WebError, match="not public"),
        ):
            await WebClient(provider="duckduckgo").fetch("https://example.com/r", max_chars=500)

    @respx.mock
    async def test_binary_content_is_refused(self):
        respx.get("https://example.com/f.pdf").mock(
            return_value=httpx.Response(
                200, headers={"content-type": "application/pdf"}, content=b"%PDF"
            )
        )
        with (
            patch("ai_agent.integrations.web._resolve", return_value=["93.184.216.34"]),
            pytest.raises(WebError, match="not text"),
        ):
            await WebClient(provider="duckduckgo").fetch("https://example.com/f.pdf", max_chars=500)

    def test_links_keep_their_targets(self):
        _, text = html_to_text(
            '<ul><li><a href="/partners/examplia">Examplia Systems</a></li>'
            '<li><a href="https://zorbix.test/">Zorbix</a></li>'
            '<li><a href="https://x.test/">https://x.test/</a></li>'
            '<li><a href="#top">Back to top</a></li></ul>',
            "https://directory.test/partners/list",
        )
        assert text == (
            "Examplia Systems (https://directory.test/partners/examplia)\n"
            "Zorbix (https://zorbix.test/)\n"
            "https://x.test/\n"
            "Back to top"
        )

    def test_html_to_text_keeps_block_structure(self):
        title, text = html_to_text(
            "<title>T</title><nav><a>Home</a><a>Docs</a></nav>"
            "<ul><li>one</li><li>two</li></ul><p>para</p>"
            "<table><tr><td>a</td><td>b</td></tr></table><footer>legal</footer>"
        )
        assert title == "T"
        assert text == "one\ntwo\n\npara\n\na b"


class TestTools:
    def test_off_registers_nothing(self):
        assert build_web_tools(_settings(web_search_provider="off")) == []

    def test_tools_are_read_only_so_no_confirmation_pauses_on_them(self):
        registry = ToolRegistry(build_web_tools(_settings(web_search_provider="duckduckgo")))
        assert registry.names() == {WEB_SEARCH_TOOL, WEB_FETCH_TOOL}
        assert not registry.writes(WEB_SEARCH_TOOL)
        assert not registry.writes(WEB_FETCH_TOOL)

    @respx.mock
    async def test_search_result_is_the_json_the_loop_reads_sources_from(self):
        respx.get("https://html.duckduckgo.com/html/").mock(
            return_value=httpx.Response(200, text=_DDG_PAGE)
        )
        registry = ToolRegistry(build_web_tools(_settings(web_search_provider="duckduckgo")))
        result = await registry.ainvoke(WEB_SEARCH_TOOL, {"query": "gst rates"})
        rows = json.loads(result.split("\n", 1)[1])
        assert rows[0] == {
            "seq": 1,
            "file": "https://example.com/gst",
            "file_name": "GST rates 2026",
            "content": "The current GST rate is 18%.",
        }

    @respx.mock
    async def test_a_failed_search_is_a_result_the_model_reads(self):
        respx.get("https://html.duckduckgo.com/html/").mock(return_value=httpx.Response(503))
        registry = ToolRegistry(build_web_tools(_settings(web_search_provider="duckduckgo")))
        result = await registry.ainvoke(WEB_SEARCH_TOOL, {"query": "q"})
        assert result.startswith(
            "Tool call failed: web search failed: The search service answered 503"
        )

    async def test_fetch_refusal_is_a_result_too(self):
        registry = ToolRegistry(build_web_tools(_settings(web_search_provider="duckduckgo")))
        result = await registry.ainvoke(WEB_FETCH_TOOL, {"url": "http://127.0.0.1:6379/"})
        assert "not public" in result


class TestSettings:
    def test_searxng_needs_a_url(self):
        with pytest.raises(ValueError, match="web_search_url"):
            _settings(web_search_provider="searxng")

    @pytest.mark.parametrize("provider", ["brave", "tavily"])
    def test_keyed_providers_need_a_key(self, provider: str):
        with pytest.raises(ValueError, match="web_search_api_key"):
            _settings(web_search_provider=provider)

    def test_unknown_provider_is_refused(self):
        with pytest.raises(ValueError, match="web_search_provider"):
            _settings(web_search_provider="bing")

    def test_default_registers_no_web_tools(self):
        s = _settings()
        assert s.web_search_provider == "off"
        assert cast(str, s.web_search_api_key) == ""
        assert build_web_tools(s) == []


class TestSafeSearch:
    """The level reaches each provider in the form that provider takes."""

    @pytest.mark.parametrize(("level", "kp"), [("off", "-2"), ("moderate", "-1"), ("strict", "1")])
    @respx.mock
    async def test_duckduckgo_kp(self, level: str, kp: str):
        route = respx.get("https://html.duckduckgo.com/html/").mock(
            return_value=httpx.Response(200, text=_DDG_PAGE)
        )
        await WebClient(provider="duckduckgo", safe_search=level).search("q")
        assert route.calls.last.request.url.params["kp"] == kp

    @pytest.mark.parametrize(("level", "value"), [("off", "0"), ("moderate", "1"), ("strict", "2")])
    @respx.mock
    async def test_searxng_safesearch(self, level: str, value: str):
        route = respx.get("http://searx.test/search").mock(
            return_value=httpx.Response(200, json={"results": []})
        )
        await WebClient(provider="searxng", base_url="http://searx.test", safe_search=level).search(
            "q"
        )
        assert route.calls.last.request.url.params["safesearch"] == value

    @pytest.mark.parametrize("level", ["off", "moderate", "strict"])
    @respx.mock
    async def test_brave_safesearch(self, level: str):
        route = respx.get("https://api.search.brave.com/res/v1/web/search").mock(
            return_value=httpx.Response(200, json={"web": {"results": []}})
        )
        await WebClient(provider="brave", api_key="k", safe_search=level).search("q")
        assert route.calls.last.request.url.params["safesearch"] == level

    def test_default_is_strict_and_unknown_levels_are_refused(self):
        assert WebClient(provider="off").safe_search == "strict"
        assert _settings().web_safe_search == "strict"
        with pytest.raises(ValueError, match="safe search"):
            WebClient(provider="off", safe_search="very")
        with pytest.raises(ValueError, match="web_safe_search"):
            _settings(web_safe_search="very")


class TestDomainPolicy:
    def test_block_list_covers_subdomains_but_not_lookalikes(self):
        c = WebClient(provider="off", blocked_domains=("bad.test",))
        assert not c.allows("https://bad.test/x")
        assert not c.allows("https://www.bad.test/x")
        assert not c.allows("https://a.b.bad.test/x")
        assert c.allows("https://notbad.test/x")
        assert c.allows("https://bad.test.example.org/x")

    def test_allow_list_admits_only_its_domains_and_block_still_wins(self):
        c = WebClient(
            provider="off",
            allowed_domains=("docs.test", "ok.test"),
            blocked_domains=("evil.docs.test",),
        )
        assert c.allows("https://docs.test/a") and c.allows("https://sub.ok.test/")
        assert not c.allows("https://other.test/")
        assert not c.allows("https://evil.docs.test/")

    def test_no_lists_allows_any_host_and_a_hostless_url_never(self):
        c = WebClient(provider="off")
        assert c.allows("https://anything.test/")
        assert not c.allows("not a url")

    def test_entries_are_normalised(self):
        c = WebClient(provider="off", blocked_domains=("*.Bad.TEST", ".other.test"))
        assert not c.allows("https://x.bad.test/") and not c.allows("https://other.test/")

    @respx.mock
    async def test_search_results_are_filtered(self):
        rows = [
            {"title": "ok", "url": "https://good.test/a", "content": ""},
            {"title": "blocked", "url": "https://www.bad.test/b", "content": ""},
        ]
        respx.get("http://searx.test/search").mock(
            return_value=httpx.Response(200, json={"results": rows})
        )
        hits = await WebClient(
            provider="searxng", base_url="http://searx.test", blocked_domains=("bad.test",)
        ).search("q")
        assert [h.url for h in hits] == ["https://good.test/a"]

    @respx.mock
    async def test_fetch_refuses_a_blocked_site_and_a_redirect_into_one(self):
        respx.get("https://good.test/r").mock(
            return_value=httpx.Response(302, headers={"location": "https://www.bad.test/x"})
        )
        client = WebClient(provider="off", blocked_domains=("bad.test",))
        with patch("ai_agent.integrations.web._resolve", return_value=["93.184.216.34"]):
            with pytest.raises(WebError, match="not allowed"):
                await client.fetch("https://bad.test/x", max_chars=300)
            with pytest.raises(WebError, match="not allowed"):
                await client.fetch("https://good.test/r", max_chars=300)

    @respx.mock
    async def test_allow_list_blocks_fetching_everything_else(self):
        client = WebClient(provider="off", allowed_domains=("docs.test",))
        with pytest.raises(WebError, match="not allowed"):
            await client.fetch("https://elsewhere.test/", max_chars=300)

    async def test_tools_are_built_with_the_settings_policy(self):
        s = _settings(
            web_search_provider="duckduckgo",
            web_blocked_domains="bad.test, *.worse.test",
            web_safe_search="moderate",
        )
        registry = ToolRegistry(build_web_tools(s))
        result = await registry.ainvoke(WEB_FETCH_TOOL, {"url": "https://x.worse.test/"})
        assert "not allowed" in result

    @pytest.mark.parametrize("bad", ["https://bad.test/path", "bad", "bad.test extra"])
    def test_malformed_domain_entries_fail_startup(self, bad: str):
        with pytest.raises(ValueError, match="not a domain"):
            _settings(web_blocked_domains=bad)
        with pytest.raises(ValueError, match="not a domain"):
            _settings(web_allowed_domains=bad)
