"""The embedding provider: batching, ordering, dimensions and failure."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from app.ai.embeddings import OpenAIEmbeddingProvider, build_embedding_provider
from app.ai.embeddings.provider import validate_dimensions, validate_input
from app.core.config import Environment, Settings
from app.core.exceptions import (
    EmbeddingDimensionError,
    EmbeddingError,
    EmptyEmbeddingInputError,
)

DIMENSIONS = 4
API_KEY = "test-embedding-key-not-used-anywhere-real"


def settings_for(
    *,
    dimensions: int = DIMENSIONS,
    batch_size: int = 2,
    api_key: str = API_KEY,
    model: str = "text-embedding-3-small",
) -> Settings:
    return Settings(
        _env_file=None,
        environment=Environment.TEST,
        embedding_api_key=api_key,  # type: ignore[arg-type]
        embedding_dimensions=dimensions,
        embedding_batch_size=batch_size,
        embedding_model=model,
    )


class RecordingTransport(httpx.AsyncBaseTransport):
    """Answers embedding requests, recording what was asked for.

    A transport rather than a patched client: the provider's own request
    building -- URL, auth header, payload shape -- is then part of what is
    under test, instead of being skipped over by a mock of the method that
    builds it.
    """

    def __init__(
        self,
        *,
        dimensions: int = DIMENSIONS,
        status_code: int = 200,
        body: dict[str, Any] | None = None,
        shuffle: bool = False,
        drop_last: bool = False,
        raise_error: bool = False,
    ) -> None:
        self.requests: list[dict[str, Any]] = []
        self.headers: list[httpx.Headers] = []
        self._dimensions = dimensions
        self._status_code = status_code
        self._body = body
        self._shuffle = shuffle
        self._drop_last = drop_last
        self._raise_error = raise_error

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if self._raise_error:
            raise httpx.ConnectError("no route to host")

        import json

        payload = json.loads(request.content)
        self.requests.append(payload)
        self.headers.append(request.headers)

        if self._body is not None or self._status_code >= 400:
            return httpx.Response(self._status_code, json=self._body or {})

        inputs = payload["input"]
        if self._drop_last:
            inputs = inputs[:-1]

        data = [
            {
                "index": position,
                "embedding": [float(position)] * self._dimensions,
            }
            for position in range(len(inputs))
        ]
        if self._shuffle:
            data.reverse()

        return httpx.Response(200, json={"data": data, "model": payload["model"]})


def provider_with(
    transport: RecordingTransport, **kwargs: Any
) -> tuple[OpenAIEmbeddingProvider, httpx.AsyncClient]:
    client = httpx.AsyncClient(transport=transport)
    return OpenAIEmbeddingProvider(settings_for(**kwargs), client), client


class TestConfiguration:
    def test_a_missing_credential_fails_at_construction(self) -> None:
        """A deployment with no key should fail to start, not accept
        documents and then fail every one of them at the embed stage."""
        with pytest.raises(ValueError, match="EMBEDDING_API_KEY"):
            OpenAIEmbeddingProvider(settings_for(api_key=""), httpx.AsyncClient())

    def test_model_and_dimensions_come_from_configuration(self) -> None:
        provider, _ = provider_with(
            RecordingTransport(), dimensions=16, model="text-embedding-3-large"
        )

        assert provider.model == "text-embedding-3-large"
        assert provider.dimensions == 16

    def test_the_factory_builds_the_configured_provider(self) -> None:
        provider = build_embedding_provider(settings_for(), httpx.AsyncClient())

        assert isinstance(provider, OpenAIEmbeddingProvider)

    async def test_the_credential_is_sent_as_a_bearer_token(self) -> None:
        transport = RecordingTransport()
        provider, client = provider_with(transport)

        async with client:
            await provider.embed_text("Acme raised $12M.")

        assert transport.headers[0]["authorization"] == f"Bearer {API_KEY}"

    async def test_the_credential_never_appears_in_an_error(self) -> None:
        transport = RecordingTransport(status_code=401)
        provider, client = provider_with(transport)

        async with client:
            with pytest.raises(EmbeddingError) as caught:
                await provider.embed_text("Acme raised $12M.")

        assert API_KEY not in str(caught.value)


class TestBatching:
    async def test_splits_input_into_batches(self) -> None:
        transport = RecordingTransport()
        provider, client = provider_with(transport, batch_size=2)

        async with client:
            await provider.embed_texts(["a", "b", "c", "d", "e"])

        # Five texts at two per request is three requests, the last partial.
        assert [len(request["input"]) for request in transport.requests] == [2, 2, 1]

    async def test_returns_one_vector_per_input(self) -> None:
        transport = RecordingTransport()
        provider, client = provider_with(transport, batch_size=2)

        async with client:
            vectors = await provider.embed_texts(["a", "b", "c", "d", "e"])

        assert len(vectors) == 5
        assert all(len(vector) == DIMENSIONS for vector in vectors)

    async def test_preserves_order_across_batches(self) -> None:
        """Batching is an implementation detail; a reordered result would
        attach each chunk's vector to a different chunk."""
        transport = RecordingTransport()
        provider, client = provider_with(transport, batch_size=2)

        async with client:
            vectors = await provider.embed_texts(["a", "b", "c"])

        # The stub encodes each text's position within its batch, so the
        # expected sequence across batches of two is 0,1 then 0.
        assert [vector[0] for vector in vectors] == [0.0, 1.0, 0.0]

    async def test_reorders_a_response_that_arrives_out_of_order(self) -> None:
        """The API documents that results may come back in any order, and a
        silently mis-ordered batch is wrong retrieval with no error."""
        transport = RecordingTransport(shuffle=True)
        provider, client = provider_with(transport, batch_size=3)

        async with client:
            vectors = await provider.embed_texts(["a", "b", "c"])

        assert [vector[0] for vector in vectors] == [0.0, 1.0, 2.0]

    async def test_a_single_batch_is_one_request(self) -> None:
        transport = RecordingTransport()
        provider, client = provider_with(transport, batch_size=10)

        async with client:
            await provider.embed_texts(["a", "b", "c"])

        assert len(transport.requests) == 1

    async def test_no_input_makes_no_request(self) -> None:
        transport = RecordingTransport()
        provider, client = provider_with(transport)

        async with client:
            assert await provider.embed_texts([]) == []

        assert transport.requests == []

    async def test_embed_text_is_embed_texts_of_one(self) -> None:
        transport = RecordingTransport()
        provider, client = provider_with(transport)

        async with client:
            vector = await provider.embed_text("Acme raised $12M.")

        assert len(vector) == DIMENSIONS
        assert transport.requests[0]["input"] == ["Acme raised $12M."]

    async def test_requests_the_configured_model_and_width(self) -> None:
        transport = RecordingTransport()
        provider, client = provider_with(transport)

        async with client:
            await provider.embed_text("Acme")

        assert transport.requests[0]["model"] == "text-embedding-3-small"
        assert transport.requests[0]["dimensions"] == DIMENSIONS

    async def test_does_not_request_a_width_from_a_model_that_lacks_it(self) -> None:
        """Older models reject the parameter outright, so it is omitted
        rather than sent hopefully."""
        transport = RecordingTransport()
        provider, client = provider_with(transport, model="text-embedding-ada-002")

        async with client:
            await provider.embed_text("Acme")

        assert "dimensions" not in transport.requests[0]


class TestDimensionChecking:
    async def test_rejects_a_vector_of_the_wrong_width(self) -> None:
        """Caught here rather than at the index, where the error would name
        the index instead of the model that caused it."""
        transport = RecordingTransport(dimensions=DIMENSIONS + 1)
        provider, client = provider_with(transport)

        async with client:
            with pytest.raises(EmbeddingDimensionError) as caught:
                await provider.embed_text("Acme")

        assert caught.value.expected == DIMENSIONS
        assert caught.value.actual == DIMENSIONS + 1

    async def test_rejects_a_response_missing_a_vector(self) -> None:
        """The dangerous shape: a caller zipping inputs to outputs would pair
        every later chunk with the wrong vector."""
        transport = RecordingTransport(drop_last=True)
        provider, client = provider_with(transport, batch_size=10)

        async with client:
            with pytest.raises(EmbeddingError, match="expected 3 vectors"):
                await provider.embed_texts(["a", "b", "c"])

    def test_the_checker_names_the_offending_position(self) -> None:
        with pytest.raises(EmbeddingDimensionError) as caught:
            validate_dimensions([[0.0, 0.0], [0.0], [0.0, 0.0]], 2, model="some-model")

        assert caught.value.index == 1

    def test_the_checker_accepts_correct_vectors(self) -> None:
        validate_dimensions([[0.0, 1.0], [2.0, 3.0]], 2, model="some-model")


class TestInputValidation:
    @pytest.mark.parametrize("blank", ["", "   ", "\n\t "])
    def test_blank_text_is_refused(self, blank: str) -> None:
        """Providers reject it too, and embedding a space would store a
        vector that means nothing while looking like it means something."""
        with pytest.raises(EmptyEmbeddingInputError):
            validate_input(["fine", blank])

    def test_the_offending_position_is_named(self) -> None:
        with pytest.raises(EmptyEmbeddingInputError) as caught:
            validate_input(["fine", "also fine", ""])

        assert caught.value.index == 2

    async def test_a_blank_input_makes_no_request(self) -> None:
        transport = RecordingTransport()
        provider, client = provider_with(transport)

        async with client:
            with pytest.raises(EmptyEmbeddingInputError):
                await provider.embed_texts(["fine", ""])

        assert transport.requests == []


class TestFailure:
    async def test_an_unreachable_provider_is_an_embedding_error(self) -> None:
        transport = RecordingTransport(raise_error=True)
        provider, client = provider_with(transport)

        async with client:
            with pytest.raises(EmbeddingError, match="unreachable"):
                await provider.embed_text("Acme")

    @pytest.mark.parametrize("status_code", [400, 401, 429, 500, 503])
    async def test_an_error_status_is_an_embedding_error(self, status_code: int) -> None:
        transport = RecordingTransport(status_code=status_code)
        provider, client = provider_with(transport)

        async with client:
            with pytest.raises(EmbeddingError) as caught:
                await provider.embed_text("Acme")

        assert str(status_code) in str(caught.value)

    async def test_an_unreadable_body_is_an_embedding_error(self) -> None:
        transport = RecordingTransport(body={"unexpected": "shape"})
        provider, client = provider_with(transport)

        async with client:
            with pytest.raises(EmbeddingError, match="unreadable"):
                await provider.embed_text("Acme")

    async def test_a_malformed_vector_is_an_embedding_error(self) -> None:
        transport = RecordingTransport(
            body={"data": [{"index": 0, "embedding": ["not", "numbers"]}]}
        )
        provider, client = provider_with(transport)

        async with client:
            with pytest.raises(EmbeddingError, match="malformed"):
                await provider.embed_text("Acme")

    async def test_an_error_does_not_quote_the_document_back(self) -> None:
        """Provider error envelopes echo the request, and the request is the
        user's document text."""
        transport = RecordingTransport(status_code=400)
        provider, client = provider_with(transport)
        secret = "Acme is acquiring Initech for $400M, strictly confidential."

        async with client:
            with pytest.raises(EmbeddingError) as caught:
                await provider.embed_text(secret)

        assert secret not in str(caught.value)
