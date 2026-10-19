"""Liveness and dependency-health endpoints."""

from __future__ import annotations

import logging
from typing import Annotated, Final

from fastapi import APIRouter, Depends

from app.api.deps import GraphDatabaseDep
from app.core.config import Settings, get_settings
from app.schemas.health import GraphHealthResponse, HealthResponse

logger: Final = logging.getLogger(__name__)

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
async def health(settings: Annotated[Settings, Depends(get_settings)]) -> HealthResponse:
    """Report that the API process is up and able to resolve its configuration."""
    return HealthResponse(status="ok", service=settings.service_name)


@router.get(
    "/health/graph",
    response_model=GraphHealthResponse,
    summary="Reachability of the graph database",
)
async def graph_health(graph: GraphDatabaseDep) -> GraphHealthResponse:
    """Report whether Neo4j is reachable and will answer a query.

    Always 200, with the state in the body. A 5xx here would make an
    orchestrator restart a process that is working correctly and is merely
    reporting that something downstream is not.

    Deliberately says only reachable or not. The URI, the version and the
    failure reason go to the logs; an unauthenticated endpoint should not
    describe the internals of what is broken.
    """
    try:
        await graph.verify()
    except Exception:
        logger.warning("Graph health check failed", exc_info=True)
        return GraphHealthResponse(status="unavailable")
    return GraphHealthResponse(status="ok")
