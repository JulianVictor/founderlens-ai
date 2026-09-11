"""Shared pytest fixtures."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.config import Environment, Settings, get_settings
from app.main import create_app


@pytest.fixture
def settings() -> Iterator[Settings]:
    """Isolated settings that ignore any developer ``.env`` on disk."""
    get_settings.cache_clear()
    yield Settings(_env_file=None, environment=Environment.TEST)
    get_settings.cache_clear()


@pytest.fixture
async def client(settings: Settings) -> AsyncIterator[AsyncClient]:
    """HTTP client bound directly to the ASGI app, no network involved."""
    app = create_app(settings)
    app.dependency_overrides[get_settings] = lambda: settings
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as async_client:
        yield async_client
