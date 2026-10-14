"""REST API endpoints for health and config."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from ai_agent.config import Settings
from ai_agent.integrations.models import ModelCatalog
from ai_agent.middleware.sid import require_sid
from ai_agent.services.health import HealthService


def create_rest_router(
    settings: Settings,
    health_service: HealthService,
    models: ModelCatalog | None = None,
) -> APIRouter:
    catalog = models or ModelCatalog(settings)
    router = APIRouter()

    # /health is the container's liveness probe and answers up or down only; every
    # route that names the model or a peer URL takes the same sid as POST /api/v1/chat
    @router.get("/health")
    async def health(detail: bool = Query(False)):
        if detail:
            return await health_service.check_all()
        return {"status": "ok"}

    @router.get("/config", dependencies=[Depends(require_sid)])
    async def config():
        return {
            "llm_provider": settings.llm_provider,
            "llm_model": settings.llm_model,
            "llm_base_url": settings.llm_base_url,
            # the context window it asks Ollama for; hosted providers set their own
            "llm_num_ctx": settings.llm_num_ctx,
            "mcp_server_url": settings.mcp_server_url,
        }

    @router.get("/api/v1/models", dependencies=[Depends(require_sid)])
    async def list_models():
        """The provider's models a user may pick, default first; discovered, never typed in."""
        return {
            "models": [{"id": m.id, "label": m.label} for m in await catalog.models()],
            "default": catalog.default,
        }

    return router
