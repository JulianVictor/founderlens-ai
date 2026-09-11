"""Tests for typed settings loading."""

from __future__ import annotations

import pytest
from pydantic import SecretStr

from app.core.config import EmbeddingProvider, Environment, LLMProvider, Settings, get_settings


def test_settings_read_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NEO4J_URI", "bolt://graph:7687")
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("LLM_API_KEY", "test-key")

    settings = Settings(_env_file=None)

    assert settings.neo4j_uri == "bolt://graph:7687"
    assert settings.llm_provider is LLMProvider.ANTHROPIC
    assert settings.llm_api_key.get_secret_value() == "test-key"


def test_secrets_are_not_rendered_in_repr() -> None:
    settings = Settings(_env_file=None, llm_api_key=SecretStr("super-secret"))

    assert "super-secret" not in repr(settings)


def test_defaults_are_local_and_inert() -> None:
    settings = Settings(_env_file=None)

    assert settings.environment is Environment.LOCAL
    assert settings.embedding_provider is EmbeddingProvider.OPENAI
    assert settings.neo4j_password.get_secret_value() == ""


def test_get_settings_is_cached() -> None:
    get_settings.cache_clear()
    try:
        assert get_settings() is get_settings()
    finally:
        get_settings.cache_clear()
