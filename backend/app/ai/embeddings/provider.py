"""The embedding provider interface.

One of the two abstractions CLAUDE.md allows before a second implementation
exists, because the provider is genuinely swappable and is selected by
configuration rather than by code.

Two guarantees every implementation owes its callers:

* **Order is preserved.** ``embed_texts`` returns one vector per input, in the
  same order. Batching is an implementation detail, and a reordered result
  would silently attach each chunk's vector to a different chunk -- a failure
  that produces plausible, wrong retrieval rather than an error.
* **Dimensions are checked.** A vector of the wrong width is rejected here
  rather than at the point it is written to an index, because by then the
  cause (a model change, a truncating gateway) is several steps away.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, runtime_checkable

from app.core.exceptions import EmbeddingDimensionError, EmptyEmbeddingInputError

#: A single embedding. A plain list of floats, because that is what both the
#: provider APIs and the Neo4j driver speak; wrapping it would buy nothing and
#: cost a conversion on every chunk.
Embedding = list[float]


@runtime_checkable
class EmbeddingProvider(Protocol):
    """Turns text into vectors."""

    @property
    def model(self) -> str:
        """Identifier recorded alongside vectors.

        Vectors from different models are not comparable, so a stored vector
        without this is a number nobody can safely use.
        """
        ...

    @property
    def dimensions(self) -> int:
        """Width of every vector this provider returns."""
        ...

    async def embed_text(self, text: str) -> Embedding:
        """Embed one string."""
        ...

    async def embed_texts(self, texts: Sequence[str]) -> list[Embedding]:
        """Embed many strings, returning one vector per input, in order."""
        ...


def validate_input(texts: Sequence[str]) -> None:
    """Reject input a provider cannot embed.

    Empty strings are refused rather than quietly substituted: the providers
    reject them too, and silently embedding a space would store a vector that
    means nothing while looking like it means something. Chunks are non-blank
    by construction, so this firing indicates a bug upstream.
    """
    for index, text in enumerate(texts):
        if not text or not text.strip():
            raise EmptyEmbeddingInputError(index)


def validate_dimensions(vectors: Sequence[Embedding], expected: int, *, model: str) -> None:
    """Check every vector's width, and that none is missing.

    A provider returning fewer vectors than inputs is the dangerous case: a
    caller zipping the two lists together would pair every chunk after the gap
    with the wrong vector.
    """
    for index, vector in enumerate(vectors):
        if len(vector) != expected:
            raise EmbeddingDimensionError(
                model=model, expected=expected, actual=len(vector), index=index
            )
