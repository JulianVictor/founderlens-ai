"""FastAPI application factory and ASGI entrypoint."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.router import api_router
from app.core.config import Settings, get_settings
from app.core.logging import configure_logging


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build a configured FastAPI application instance."""
    settings = settings or get_settings()
    configure_logging(settings)

    app = FastAPI(
        title="FounderLens AI API",
        version="0.1.0",
        summary="Evidence-grounded startup research over documents and a knowledge graph.",
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.cors_allow_origins),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(api_router)
    return app


app = create_app()
