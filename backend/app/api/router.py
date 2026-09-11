"""Aggregate router mounted by the FastAPI application."""

from __future__ import annotations

from fastapi import APIRouter

from app.api.routes import health

api_router = APIRouter()
api_router.include_router(health.router)
