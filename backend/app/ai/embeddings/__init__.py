"""Embedding generation.

A provider interface and the implementations behind it. Which provider and
which model are used comes entirely from configuration -- ``EMBEDDING_PROVIDER``
and ``EMBEDDING_MODEL`` -- and the credential from ``EMBEDDING_API_KEY``.
Nothing here carries a default that would work: a deployment that has not been
configured fails at startup rather than embedding against somebody's account.
"""

from __future__ import annotations

import httpx

from app.ai.embeddings.openai_provider import OpenAIEmbeddingProvider
from app.ai.embeddings.provider import (
    Embedding,
    EmbeddingProvider,
    validate_dimensions,
    validate_input,
)
from app.core.config import EmbeddingProvider as EmbeddingProviderName
from app.core.config import Settings


def build_embedding_provider(settings: Settings, client: httpx.AsyncClient) -> EmbeddingProvider:
    """The provider named by ``EMBEDDING_PROVIDER``.

    A match rather than a registry: there is one implementation, and a
    registry would be indirection standing in for a decision that has not been
    made yet. An unhandled value is a configuration error, raised here instead
    of falling through to a default that would quietly use the wrong service.
    """
    match settings.embedding_provider:
        case EmbeddingProviderName.OPENAI:
            return OpenAIEmbeddingProvider(settings, client)
        case unknown:  # pragma: no cover - unreachable while the enum has one member
            raise ValueError(f"Unsupported embedding provider: {unknown}")


__all__ = [
    "Embedding",
    "EmbeddingProvider",
    "OpenAIEmbeddingProvider",
    "build_embedding_provider",
    "validate_dimensions",
    "validate_input",
]
