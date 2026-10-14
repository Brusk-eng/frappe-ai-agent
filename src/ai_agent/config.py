"""Application configuration via environment variables."""

from __future__ import annotations

from pathlib import Path

from limits import parse_many
from limits.errors import ConfigurationError
from limits.storage import storage_from_string
from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Absolute, so .env is found whatever the process CWD; a missing .env is skipped.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

# The web search backends integrations/web.py implements; `off` registers no web tools.
WEB_SEARCH_PROVIDERS = ("duckduckgo", "searxng", "brave", "tavily", "off")


class Settings(BaseSettings):
    # No extra="ignore": an orphan AI_AGENT_* key left in .env must fail startup, not fall
    # back to the default. Unknown keys in os.environ are dropped before validation either way.
    model_config = SettingsConfigDict(
        env_prefix="AI_AGENT_",
        env_file=_PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
    )

    @field_validator("agent_rate_limit")
    @classmethod
    def _rate_limit_parses(cls, v: str) -> str:
        # slowapi stops limiting on a string it cannot read, so refuse one at startup
        parse_many(v)
        return v

    @field_validator("rate_limit_storage_uri")
    @classmethod
    def _storage_uri_opens(cls, v: str) -> str:
        # a uri limits cannot read leaves every process counting for itself, which looks like a
        # working limit until the second worker starts; refuse it at startup instead
        try:
            storage_from_string(v)
        except ConfigurationError as exc:
            raise ValueError(f"rate_limit_storage_uri: {exc}") from exc
        return v

    # LLM
    llm_provider: str = "ollama"
    llm_base_url: str = "http://localhost:11434"
    llm_api_key: str = ""
    llm_model: str = "qwen3.5:9b"
    # Low for tool-call arguments: qwen3.5:9b wrote malformed tool-call XML at 0.7.
    llm_temperature: float = 0.2
    llm_max_tokens: int = 8192
    # Ollama only. Its default context is too small for the system prompt plus tool results
    # and truncates silently.
    llm_num_ctx: int = 16384
    # One model call's bound. Ollama's own client defaults to no timeout at all.
    llm_request_timeout_s: float = 60.0

    # Agent
    # The turn's wall-clock bound, inside frappe_ai's 120 s so its message still reaches the user.
    agent_turn_timeout_s: float = 90.0
    # The longest tool result or history row that may enter the prompt; the rest is cut.
    agent_prompt_text_max_chars: int = 8000
    # Small models loop exploring schema; the loop runs this // 2 model-tool rounds per turn.
    agent_recursion_limit: int = 50
    # Per-sid rate limit on POST /api/v1/chat. slowapi syntax: "<count>/<period>"
    # (minute / second / hour / day).
    agent_rate_limit: str = "30/minute"
    # Where the limiter keeps its counters. The default is this process, so the limit is
    # agent_rate_limit x workers x replicas; point every process at one redis:// to share it.
    rate_limit_storage_uri: str = "memory://"

    # MCP: Streamable HTTP endpoint. frappe-mcp-server mounts /mcp on its
    # main HTTP port (default 8080), NOT the port+1 MCP-protocol-only server.
    mcp_server_url: str = "http://localhost:8080/mcp"

    # Longer than the MCP server's sid check; a timeout fails the turn (services/chat.py).
    mcp_tools_load_timeout_s: float = 20.0

    # One tool call's bound, on the call and on the session under it.
    mcp_tool_timeout_s: float = 30.0

    # Web: the agent's own search and page read, built in (agent/web_tools.py), no MCP server.
    # `off` (the default) registers no web tools; `searxng` needs web_search_url; `brave` and
    # `tavily` need web_search_api_key; `duckduckgo` needs nothing but is refused a human check
    # from most server networks, so it is a laptop option, not a deployment one.
    web_search_provider: str = "off"
    web_search_url: str = ""
    web_search_api_key: str = ""
    # One search or page fetch's bound; inside the tool-call bound, so a slow site is a result.
    web_timeout_s: float = 10.0

    @model_validator(mode="after")
    def _validate_web(self) -> Settings:
        if self.web_search_provider not in WEB_SEARCH_PROVIDERS:
            raise ValueError(f"web_search_provider must be one of {WEB_SEARCH_PROVIDERS}")
        if self.web_search_provider == "searxng" and not self.web_search_url:
            raise ValueError("web_search_url is required when web_search_provider is searxng")
        if self.web_search_provider in ("brave", "tavily") and not self.web_search_api_key:
            raise ValueError(
                "web_search_api_key is required when web_search_provider is "
                + self.web_search_provider
            )
        if self.web_timeout_s <= 0:
            raise ValueError("web_timeout_s must be positive")
        return self

    # Each /health probe's httpx timeout, per operation. The two run at once, so a hung
    # dependency answers ok: False inside the 5 s rag allows the call (rag/rag/status.py).
    health_probe_timeout_s: float = 2.0

    # Frappe URL and multi-tenant routing for chat history and session validation
    frappe_url: str = "http://localhost:8000"
    frappe_site_routes_file: str = ""
    frappe_site_url_template: str = ""

    @field_validator("frappe_site_routes_file")
    @classmethod
    def _validate_routes_file(cls, v: str) -> str:
        if not v:
            return v
        path = Path(v)
        if not path.is_file():
            raise ValueError(f"frappe_site_routes_file does not exist or is not a file: {v}")
        return v

    @field_validator("frappe_site_url_template")
    @classmethod
    def _validate_url_template(cls, v: str) -> str:
        if not v:
            return v
        if "{site}" not in v:
            raise ValueError("frappe_site_url_template must contain '{site}' placeholder")
        return v

    # Observability
    otel_endpoint: str = ""
    otel_service_name: str = "frappe-ai-agent"
    log_level: str = "info"
    log_format: str = "json"
