"""The agent's own web tools: a search and a page read, registered beside the MCP tools.

They are plain LangChain tools built in this process, so no MCP server has to be added
for the assistant to look something up outside the database. Both are declared read-only,
so the write confirmation never pauses on them.
"""

from __future__ import annotations

from langchain_core.tools import BaseTool, StructuredTool
from pydantic import BaseModel, Field

from ai_agent.config import Settings
from ai_agent.integrations.web import WebClient, WebError, hits_as_json

WEB_SEARCH_TOOL = "web_search"
WEB_FETCH_TOOL = "fetch_web_page"

READ_ONLY = {"readOnlyHint": True}


class _SearchArgs(BaseModel):
    query: str = Field(description="What to search the web for, as a short search query")


class _FetchArgs(BaseModel):
    url: str = Field(description="The http(s) address of a public web page to read")
    offset: int = Field(
        default=0,
        ge=0,
        description="Character to start from, for a long page: the previous read says which",
    )


def build_web_tools(settings: Settings) -> list[BaseTool]:
    """The web tools for this deployment, or none when the provider is `off`."""
    if settings.web_search_provider == "off":
        return []
    client = WebClient(
        provider=settings.web_search_provider,
        base_url=settings.web_search_url,
        api_key=settings.web_search_api_key,
        timeout_s=settings.web_timeout_s,
    )
    max_chars = settings.agent_prompt_text_max_chars

    async def _search(query: str) -> str:
        try:
            hits = await client.search(query)
        except WebError as exc:
            raise RuntimeError(f"web search failed: {exc}") from exc
        if not hits:
            return f'No web results for "{query}".'
        return f'Web results for "{query}", most relevant first:\n{hits_as_json(hits)}'

    async def _fetch(url: str, offset: int = 0) -> str:
        try:
            return await client.fetch(url, max_chars=max_chars, offset=offset)
        except WebError as exc:
            raise RuntimeError(f"page fetch failed: {exc}") from exc

    return [
        StructuredTool(
            name=WEB_SEARCH_TOOL,
            description=(
                "Search the public web. Use it for anything outside this ERPNext database: "
                "documentation, regulations, rates, news, general knowledge. Returns up to five "
                "results as a JSON list of {seq, file (the URL), file_name (the title), content "
                "(a snippet)}. Read a result in full with fetch_web_page."
            ),
            args_schema=_SearchArgs,
            coroutine=_search,
            metadata=READ_ONLY,
        ),
        StructuredTool(
            name=WEB_FETCH_TOOL,
            description=(
                "Read one public web page as plain text, a few thousand characters at a time. "
                "Use it on a web_search result when the snippet is not enough; when the result "
                "ends with 'more characters', call again with the offset it names."
            ),
            args_schema=_FetchArgs,
            coroutine=_fetch,
            metadata=READ_ONLY,
        ),
    ]
