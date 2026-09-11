"""Liveness endpoint."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from app.core.config import Settings, get_settings
from app.schemas.health import HealthResponse

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
async def health(settings: Annotated[Settings, Depends(get_settings)]) -> HealthResponse:
    """Report that the API process is up and able to resolve its configuration."""
    return HealthResponse(status="ok", service=settings.service_name)
