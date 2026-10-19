"""Embeddings from an OpenAI-compatible endpoint.

Spoken over HTTP with httpx rather than through the vendor SDK: the call is one
POST, and going direct keeps the credential handling, the batching and the
failure modes visible instead of inside a client that would have to be
configured and mocked anyway. It also means any OpenAI-compatible endpoint --
Azure, a gateway, a local server -- works by changing a URL.

**Credentials come from configuration only.** The key is read from settings,
has no usable default, and never appears in a log line or an error message.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any, Final

import httpx

from app.ai.embeddings.provider import Embedding, validate_dimensions, validate_input
from app.core.config import Settings
from app.core.exceptions import EmbeddingError

logger: Final = logging.getLogger(__name__)


class OpenAIEmbeddingProvider:
    """Embeddings from the OpenAI embeddings API."""

    def __init__(self, settings: Settings, client: httpx.AsyncClient) -> None:
        api_key = settings.embedding_api_key.get_secret_value()
        if not api_key:
            # Failing at construction turns a misconfigured deployment into a
            # startup error, rather than a service that accepts documents and
            # then fails every one of them at the embed stage.
            raise ValueError("EMBEDDING_API_KEY is not configured; documents cannot be embedded")

        self._api_key = api_key
        self._model = settings.embedding_model
        self._dimensions = settings.embedding_dimensions
        self._base_url = settings.embedding_base_url.rstrip("/")
        self._batch_size = settings.embedding_batch_size
        self._timeout = settings.embedding_timeout_seconds
        self._client = client

    @property
    def model(self) -> str:
        return self._model

    @property
    def dimensions(self) -> int:
        return self._dimensions

    @property
    def batch_size(self) -> int:
        return self._batch_size

    async def embed_text(self, text: str) -> Embedding:
        vectors = await self.embed_texts([text])
        return vectors[0]

    async def embed_texts(self, texts: Sequence[str]) -> list[Embedding]:
        """Embed every text, in batches, preserving order.

        Batches are issued one after another rather than concurrently.
        Embedding providers rate-limit per minute, and the document that fires
        twenty parallel requests is the one most likely to be throttled --
        turning a slow ingestion into a failed one.
        """
        if not texts:
            return []

        validate_input(texts)

        vectors: list[Embedding] = []
        for start in range(0, len(texts), self._batch_size):
            batch = list(texts[start : start + self._batch_size])
            vectors.extend(await self._embed_batch(batch))

        if len(vectors) != len(texts):
            # Fewer vectors than inputs is the dangerous shape: a caller
            # zipping the two lists together would pair every later chunk
            # with the wrong vector, which reads as working retrieval.
            raise EmbeddingError(
                self._model,
                f"expected {len(texts)} vectors, received {len(vectors)}",
            )

        validate_dimensions(vectors, self._dimensions, model=self._model)
        return vectors

    async def _embed_batch(self, batch: list[str]) -> list[Embedding]:
        payload: dict[str, Any] = {"model": self._model, "input": batch}
        if self._supports_dimensions():
            payload["dimensions"] = self._dimensions

        try:
            response = await self._client.post(
                f"{self._base_url}/embeddings",
                json=payload,
                headers={"Authorization": f"Bearer {self._api_key}"},
                timeout=self._timeout,
            )
        except httpx.HTTPError as exc:
            logger.exception("Embedding request failed")
            raise EmbeddingError(self._model, "provider unreachable") from exc

        if response.status_code >= 400:
            # The status is logged; the body is not. Provider error envelopes
            # can echo the request back, and the request contains document
            # text.
            logger.error(
                "Embedding request rejected with %s for model %s",
                response.status_code,
                self._model,
            )
            raise EmbeddingError(self._model, f"provider returned {response.status_code}")

        return self._parse(response)

    def _supports_dimensions(self) -> bool:
        """Whether the configured model accepts a ``dimensions`` parameter.

        Only the third-generation OpenAI embedding models do; sending it to an
        older one is rejected outright, so it is omitted rather than sent
        hopefully.
        """
        return self._model.startswith("text-embedding-3")

    def _parse(self, response: httpx.Response) -> list[Embedding]:
        try:
            items = response.json()["data"]
        except (ValueError, KeyError, TypeError) as exc:
            raise EmbeddingError(self._model, "unreadable response") from exc

        if not isinstance(items, list):
            raise EmbeddingError(self._model, "unreadable response")

        # Ordered by the index the provider reports rather than by array
        # position. The API documents that results may arrive out of order,
        # and a silently mis-ordered batch attaches each chunk's vector to a
        # different chunk -- wrong retrieval, with no error anywhere.
        try:
            ordered = sorted(items, key=lambda item: int(item["index"]))
            return [[float(value) for value in item["embedding"]] for item in ordered]
        except (KeyError, TypeError, ValueError) as exc:
            raise EmbeddingError(self._model, "malformed embedding payload") from exc
