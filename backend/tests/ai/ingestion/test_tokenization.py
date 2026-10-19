"""The tokenizer abstraction and its offline fallback."""

from __future__ import annotations

import pytest

from app.ai.ingestion import (
    DEFAULT_ENCODING,
    HeuristicTokenizer,
    Tokenizer,
    get_tokenizer,
)


class TestHeuristicTokenizer:
    def test_empty_text_is_zero_tokens(self) -> None:
        assert HeuristicTokenizer().count("") == 0

    def test_counts_grow_with_length(self) -> None:
        tokenizer = HeuristicTokenizer()

        assert tokenizer.count("Acme") < tokenizer.count(
            "Acme Robotics builds warehouse automation hardware"
        )

    def test_is_deterministic(self) -> None:
        """Chunk boundaries depend on this, so a varying count would make
        ingestion non-reproducible."""
        tokenizer = HeuristicTokenizer()
        text = "Acme raised $12M in March 2026, led by Initech Ventures."

        assert tokenizer.count(text) == tokenizer.count(text)

    def test_any_non_empty_text_counts_as_at_least_one_token(self) -> None:
        """Zero would let the packer loop: a piece costing nothing never fills
        a chunk."""
        for text in (" ", "\n", "a", "—"):
            assert HeuristicTokenizer().count(text) >= 1

    def test_punctuation_is_counted(self) -> None:
        tokenizer = HeuristicTokenizer()

        assert tokenizer.count("Acme, Inc.") > tokenizer.count("Acme")

    def test_newlines_are_counted(self) -> None:
        """Blank lines between paragraphs are real tokens in a BPE vocabulary,
        and ignoring them made chunks overrun their budget."""
        tokenizer = HeuristicTokenizer()

        assert tokenizer.count("a\n\n\n\nb") > tokenizer.count("a b")

    def test_long_words_cost_more_than_one_token(self) -> None:
        tokenizer = HeuristicTokenizer()

        assert tokenizer.count("antidisestablishmentarianism") > 1

    def test_estimate_is_in_the_right_order_of_magnitude(self) -> None:
        """It is an approximation and documented as one, but an approximation
        that was off by several times would size chunks uselessly."""
        text = (
            "Acme Robotics builds warehouse automation hardware for mid-market "
            "logistics operators. The Series A closed in March 2026 at twelve "
            "million dollars, led by Initech Ventures."
        )
        estimate = HeuristicTokenizer().count(text)

        # Roughly one token per four characters for English prose.
        assert len(text) / 8 < estimate < len(text) / 2

    def test_names_itself(self) -> None:
        assert HeuristicTokenizer().name == "heuristic"


class TestTokenizerSelection:
    def test_returns_something_usable_in_any_environment(self) -> None:
        """Ingestion must not be blocked by an unreachable encoding table: a
        slightly imprecise chunk is better than no chunk at all."""
        tokenizer = get_tokenizer()

        assert isinstance(tokenizer, Tokenizer)
        assert tokenizer.count("Acme raised twelve million dollars.") > 0

    def test_falls_back_and_says_so(self, caplog: pytest.LogCaptureFixture) -> None:
        """A fallback that happened silently would be discovered later, from
        odd chunk sizes, instead of from logs."""
        with caplog.at_level("WARNING"):
            tokenizer = get_tokenizer("definitely-not-an-encoding")

        assert tokenizer.name == "heuristic"
        assert "falling back" in caplog.text

    def test_the_default_encoding_matches_the_configured_embedding_model(self) -> None:
        from app.core.config import Environment, Settings

        settings = Settings(_env_file=None, environment=Environment.TEST)

        assert settings.tokenizer_encoding == DEFAULT_ENCODING
        assert settings.embedding_model.startswith("text-embedding-3")


class TestRealTokenizer:
    def test_counts_match_tiktoken_when_it_is_available(self) -> None:
        """Skipped rather than failed offline: tiktoken downloads its encoding
        table on first use, and that failing says nothing about this code."""
        tiktoken = pytest.importorskip("tiktoken")
        try:
            encoding = tiktoken.get_encoding(DEFAULT_ENCODING)
        except Exception as exc:  # pragma: no cover - environment dependent
            pytest.skip(f"{DEFAULT_ENCODING} unavailable: {exc}")

        from app.ai.ingestion import TiktokenTokenizer

        text = "Acme raised $12M in March 2026, led by Initech Ventures."
        assert TiktokenTokenizer().count(text) == len(encoding.encode(text))

    def test_text_containing_a_special_token_spelling_is_data(self) -> None:
        """A document is data; it is not addressing the tokenizer. Encoding
        would otherwise raise on a file that happens to contain the literal."""
        pytest.importorskip("tiktoken")
        try:
            from app.ai.ingestion import TiktokenTokenizer

            tokenizer = TiktokenTokenizer()
        except Exception as exc:  # pragma: no cover - environment dependent
            pytest.skip(f"tiktoken unavailable: {exc}")

        assert tokenizer.count("the text <|endoftext|> appears here") > 0
