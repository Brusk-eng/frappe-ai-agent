"""The models a user may pick, discovered from the provider instead of configured by hand.

An OpenAI-compatible provider answers the standard `GET {base_url}/models` (OpenAI, OpenRouter,
vLLM, LiteLLM and others); Ollama answers `GET {base_url}/api/tags`. The list is cached, and
the configured default model is always offered first. Where the provider says what a model
supports (OpenRouter's `supported_parameters`), a model without structured output is dropped,
since the agent's loop needs it. `llm_models_filter` narrows a long list by id prefix or exact id.

Discovery never fails a turn: an unreachable provider, an unknown answer or a provider with no
list endpoint leaves the default model as the only one offered.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any

import httpx
import structlog

from ai_agent.config import Settings

logger = structlog.get_logger(__name__)

_CACHE_SECONDS = 600
_TIMEOUT_S = 10.0
# Parameters that mean a model can answer in the JSON schema the agent loop asks for.
_STRUCTURED = frozenset({"structured_outputs", "response_format"})


@dataclass(frozen=True)
class ModelInfo:
    id: str
    label: str


def _filters(raw: str) -> tuple[str, ...]:
    return tuple(part.strip() for part in raw.split(",") if part.strip())


def _kept(model_id: str, filters: tuple[str, ...]) -> bool:
    return not filters or any(model_id == f or model_id.startswith(f) for f in filters)


def parse_openai_models(body: Any) -> list[ModelInfo]:
    """The usable models in an OpenAI-style `{"data": [{"id": ...}, ...]}` answer."""
    rows = body.get("data") if isinstance(body, dict) else None
    out: list[ModelInfo] = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict) or not isinstance(row.get("id"), str) or not row["id"]:
            continue
        supported = row.get("supported_parameters")
        if isinstance(supported, list) and not _STRUCTURED & set(supported):
            continue  # the provider says this one cannot answer in a schema
        out.append(ModelInfo(id=row["id"], label=str(row.get("name") or row["id"])))
    return out


def parse_ollama_tags(body: Any) -> list[ModelInfo]:
    """The models in an Ollama `{"models": [{"name": ...}, ...]}` answer."""
    rows = body.get("models") if isinstance(body, dict) else None
    return [
        ModelInfo(id=row["name"], label=row["name"])
        for row in (rows if isinstance(rows, list) else [])
        if isinstance(row, dict) and isinstance(row.get("name"), str) and row["name"]
    ]


class ModelCatalog:
    """The provider's models, asked for at most once per cache period, default first."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._cached: list[ModelInfo] | None = None
        self._fetched_at = 0.0
        self._lock = asyncio.Lock()

    @property
    def default(self) -> str:
        return self._settings.llm_model

    async def models(self) -> list[ModelInfo]:
        async with self._lock:
            if self._cached is None or time.monotonic() - self._fetched_at > _CACHE_SECONDS:
                self._cached = await self._discover()
                self._fetched_at = time.monotonic()
            return self._cached

    async def offers(self, model: Any) -> bool:
        return isinstance(model, str) and any(m.id == model for m in await self.models())

    async def _discover(self) -> list[ModelInfo]:
        found: list[ModelInfo] = []
        try:
            found = await self._ask_provider()
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning(
                "model_discovery_failed", error_type=type(exc).__name__, error=str(exc)[:200]
            )
        filters = _filters(self._settings.llm_models_filter)
        kept = [m for m in found if _kept(m.id, filters)]
        default = next(
            (m for m in kept if m.id == self.default), ModelInfo(self.default, self.default)
        )
        rest = sorted((m for m in kept if m.id != self.default), key=lambda m: m.label.lower())
        logger.info("models_discovered", provider_listed=len(found), offered=len(rest) + 1)
        return [default, *rest]

    async def _ask_provider(self) -> list[ModelInfo]:
        base = self._settings.llm_base_url.rstrip("/")
        provider = self._settings.llm_provider.lower()
        headers = {"Accept": "application/json"}
        if self._settings.llm_api_key:
            headers["Authorization"] = f"Bearer {self._settings.llm_api_key}"
        async with httpx.AsyncClient(timeout=_TIMEOUT_S) as client:
            if provider == "ollama":
                response = await client.get(f"{base}/api/tags", headers=headers)
                response.raise_for_status()
                return parse_ollama_tags(response.json())
            if provider == "anthropic":
                # Anthropic's own list takes its own headers; same shape as OpenAI's
                anthropic = {
                    "x-api-key": self._settings.llm_api_key,
                    "anthropic-version": "2023-06-01",
                }
                response = await client.get(f"{base}/v1/models", headers=anthropic)
                response.raise_for_status()
                return parse_openai_models(response.json())
            response = await client.get(f"{base}/models", headers=headers)
            response.raise_for_status()
            return parse_openai_models(response.json())
