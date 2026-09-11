"""FastAPI application factory and ASGI entrypoint."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.errors import register_exception_handlers
from app.api.router import api_router
from app.core.config import Settings, get_settings
from app.core.db import Database
from app.core.logging import configure_logging


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build a configured FastAPI application instance."""
    resolved = settings or get_settings()
    configure_logging(resolved)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        """Open the database pool for the life of the process.

        Connecting at startup means a bad DATABASE_URL fails the deployment
        rather than the first request that needs a workspace.
        """
        database: Database = app.state.database
        await database.connect()
        try:
            yield
        finally:
            await database.disconnect()

    app = FastAPI(
        title="FounderLens AI API",
        version="0.1.0",
        summary="Evidence-grounded startup research over documents and a knowledge graph.",
        lifespan=lifespan,
    )
    # Held on app.state rather than in a module global so that two apps in one
    # process (as in tests) do not share a pool.
    app.state.database = Database(resolved)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(resolved.cors_allow_origins),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    register_exception_handlers(app)
    app.include_router(api_router)
    return app


app = create_app()
