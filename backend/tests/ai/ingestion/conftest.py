"""Fixtures for the ingestion tests."""

from __future__ import annotations

from pathlib import Path
from uuid import UUID

import pytest

from app.ai.ingestion import (
    ChunkerConfig,
    HeuristicTokenizer,
    ParserRegistry,
    TextChunker,
    TextCleaner,
    Tokenizer,
    build_default_registry,
)

FIXTURE_DIR = Path(__file__).resolve().parents[2] / "fixtures" / "documents"

DOCUMENT_ID = UUID("00000000-0000-4000-8000-0000000000d1")
OTHER_DOCUMENT_ID = UUID("00000000-0000-4000-8000-0000000000d2")
WORKSPACE_ID = UUID("00000000-0000-4000-8000-0000000000a1")


def fixture_bytes(name: str) -> bytes:
    return (FIXTURE_DIR / name).read_bytes()


@pytest.fixture
def tokenizer() -> Tokenizer:
    """The offline tokenizer.

    Tests use the heuristic rather than tiktoken so they are deterministic and
    need no network: tiktoken downloads its encoding table on first use, which
    would make the suite fail on an isolated machine for a reason unrelated to
    the code under test. One test covers the real encoding when it is
    available.
    """
    return HeuristicTokenizer()


@pytest.fixture
def registry() -> ParserRegistry:
    return build_default_registry()


@pytest.fixture
def cleaner() -> TextCleaner:
    return TextCleaner()


@pytest.fixture
def chunker(tokenizer: Tokenizer) -> TextChunker:
    """A small-window chunker, so fixtures produce several chunks.

    The production default of 800/120 would fit each sample document in a
    single chunk, which would exercise none of the packing, overlap or
    ordering behaviour.
    """
    return TextChunker(tokenizer, ChunkerConfig(chunk_size_tokens=40, chunk_overlap_tokens=10))
