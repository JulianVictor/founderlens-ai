"""Typed application settings.

Configuration is read from environment variables (or a local ``.env`` file) and
validated once at import time via :func:`get_settings`. No secret ever has a
usable default — placeholders here are inert and must be overridden per
environment.
"""

from __future__ import annotations

from enum import StrEnum
from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Environment(StrEnum):
    """Deployment environment the API is running in."""

    LOCAL = "local"
    TEST = "test"
    STAGING = "staging"
    PRODUCTION = "production"


class LLMProvider(StrEnum):
    """Supported chat-completion providers."""

    OPENAI = "openai"
    ANTHROPIC = "anthropic"


class EmbeddingProvider(StrEnum):
    """Supported embedding providers."""

    OPENAI = "openai"


class Settings(BaseSettings):
    """Validated runtime configuration for the FounderLens API."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        frozen=True,
    )

    # --- Application ---------------------------------------------------
    service_name: str = "founderlens-api"
    environment: Environment = Environment.LOCAL
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    cors_allow_origins: tuple[str, ...] = ("http://localhost:3000",)

    # --- Application data store ----------------------------------------
    database_url: SecretStr = SecretStr("postgresql://postgres:postgres@localhost:5432/postgres")

    # --- Supabase -------------------------------------------------------
    supabase_url: str = "http://localhost:54321"
    supabase_anon_key: SecretStr = SecretStr("")
    supabase_service_role_key: SecretStr = SecretStr("")

    # --- Neo4j ----------------------------------------------------------
    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_username: str = "neo4j"
    neo4j_password: SecretStr = SecretStr("")

    # --- LLM provider ---------------------------------------------------
    llm_provider: LLMProvider = LLMProvider.OPENAI
    llm_model: str = "gpt-4o-mini"
    llm_api_key: SecretStr = SecretStr("")

    # --- Embedding provider ---------------------------------------------
    embedding_provider: EmbeddingProvider = EmbeddingProvider.OPENAI
    embedding_model: str = "text-embedding-3-small"
    embedding_dimensions: int = Field(default=1536, gt=0)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings singleton.

    Cached so that FastAPI dependency injection resolves to the same instance
    for every request; tests may clear the cache to override configuration.
    """
    return Settings()
