"""Aggregate router mounted by the FastAPI application."""

from __future__ import annotations

from fastapi import APIRouter

from app.api.routes import health
from app.api.routes.v1 import me, workspaces

# Liveness sits outside the versioned API: it describes the process, not the
# product, and a load balancer probing it should not have to track API versions.
api_router = APIRouter()
api_router.include_router(health.router)

v1_router = APIRouter(prefix="/api/v1")
v1_router.include_router(me.router)
v1_router.include_router(workspaces.router)

api_router.include_router(v1_router)
