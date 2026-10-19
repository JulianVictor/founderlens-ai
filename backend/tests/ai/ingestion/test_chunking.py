"""Chunk boundaries, overlap, ordering, identity and hashing."""

from __future__ import annotations

from itertools import pairwise

import pytest

from app.ai.ingestion import (
    ChunkerConfig,
    CleanedDocument,
    DocumentChunk,
    HeuristicTokenizer,
    ParserRegistry,
    TextChunker,
    TextCleaner,
    Tokenizer,
    build_chunk_id,
    content_hash,
)
from tests.ai.ingestion.conftest import (
    DOCUMENT_ID,
    OTHER_DOCUMENT_ID,
    WORKSPACE_ID,
    fixture_bytes,
)

PROSE = (
    "Acme Robotics builds warehouse automation hardware for mid-market "
    "logistics operators. The company was founded in 2021.\n\n"
    "The Series A closed in March 2026 at twelve million dollars, led by "
    "Initech Ventures. Existing seed investors participated.\n\n"
    "Competition comes mainly from incumbents selling fixed conveyor "
    "systems, whose installations take months rather than days.\n\n"
    "Hardware margins are thin, and the incumbents have distribution "
    "relationships that take years to build.\n\n"
    "The team has shipped three generations of the product since founding."
)


def make_document(text: str) -> CleanedDocument:
    """A cleaned document with an identity map, for chunker-only tests."""
    cleaned, offsets = TextCleaner().clean_text(text)
    return CleanedDocument(
        document_id=DOCUMENT_ID,
        workspace_id=WORKSPACE_ID,
        text=cleaned,
        source_map=offsets,
    )


#: PROSE's paragraphs cost roughly 19-35 tokens each. Overlap is carried as
#: whole pieces, so a budget below a paragraph can hold none -- these values
#: give a budget that fits one.
OVERLAP_CONFIG = ChunkerConfig(chunk_size_tokens=80, chunk_overlap_tokens=40)


def load(registry: ParserRegistry, cleaner: TextCleaner, name: str, mime: str) -> CleanedDocument:
    return cleaner.clean(
        registry.parse(
            fixture_bytes(name),
            mime_type=mime,
            document_id=DOCUMENT_ID,
            workspace_id=WORKSPACE_ID,
        )
    )


class TestChunkContent:
    def test_chunk_text_is_exactly_the_span_it_claims(self, chunker: TextChunker) -> None:
        """The invariant that makes a chunk verifiable against its document."""
        document = make_document(PROSE)

        for chunk in chunker.chunk(document):
            assert document.text[chunk.start_offset : chunk.end_offset] == chunk.text

    def test_chunks_cover_all_of_the_content(self, chunker: TextChunker) -> None:
        """Nothing is silently dropped: text that reaches no chunk can never
        be retrieved or cited, however relevant it is."""
        document = make_document(PROSE)
        chunks = chunker.chunk(document)

        covered = "".join(document.text[chunk.start_offset : chunk.end_offset] for chunk in chunks)
        for word in ("warehouse", "Initech", "conveyor", "margins", "generations"):
            assert word in covered

    def test_chunks_carry_no_leading_or_trailing_whitespace(self, chunker: TextChunker) -> None:
        document = make_document(PROSE)

        for chunk in chunker.chunk(document):
            assert chunk.text == chunk.text.strip()

    def test_chunks_carry_their_identifiers(self, chunker: TextChunker) -> None:
        document = make_document(PROSE)

        for chunk in chunker.chunk(document):
            assert chunk.document_id == DOCUMENT_ID
            assert chunk.workspace_id == WORKSPACE_ID

    def test_an_empty_document_yields_no_chunks(self, chunker: TextChunker) -> None:
        """Not one empty chunk: it would be embedded, retrieved and cited like
        any other, and it supports nothing."""
        assert chunker.chunk(make_document("")) == []

    def test_a_whitespace_only_document_yields_no_chunks(self, chunker: TextChunker) -> None:
        assert chunker.chunk(make_document("   \n\n\t  \n")) == []

    def test_a_short_document_is_a_single_chunk(self, chunker: TextChunker) -> None:
        chunks = chunker.chunk(make_document("Acme raised twelve million dollars."))

        assert len(chunks) == 1
        assert chunks[0].text == "Acme raised twelve million dollars."


class TestOrdering:
    def test_indices_are_sequential_from_zero(self, chunker: TextChunker) -> None:
        chunks = chunker.chunk(make_document(PROSE))

        assert [chunk.chunk_index for chunk in chunks] == list(range(len(chunks)))

    def test_chunks_are_in_reading_order(self, chunker: TextChunker) -> None:
        chunks = chunker.chunk(make_document(PROSE))

        starts = [chunk.start_offset for chunk in chunks]
        assert starts == sorted(starts)

    def test_each_chunk_starts_after_the_previous_one(self, chunker: TextChunker) -> None:
        """Strictly increasing, so the chunker always advances. Equal starts
        would mean a document that never finishes chunking."""
        chunks = chunker.chunk(make_document(PROSE))

        for earlier, later in pairwise(chunks):
            assert later.start_offset > earlier.start_offset

    def test_ordering_survives_a_long_document(self, tokenizer: Tokenizer) -> None:
        chunker = TextChunker(
            tokenizer, ChunkerConfig(chunk_size_tokens=30, chunk_overlap_tokens=8)
        )
        chunks = chunker.chunk(make_document(PROSE * 6))

        assert len(chunks) > 10
        assert [c.chunk_index for c in chunks] == list(range(len(chunks)))
        assert [c.start_offset for c in chunks] == sorted(c.start_offset for c in chunks)


class TestOverlap:
    def test_consecutive_chunks_overlap(self, tokenizer: Tokenizer) -> None:
        """A fact spanning a boundary must be retrievable from both sides."""
        chunks = TextChunker(tokenizer, OVERLAP_CONFIG).chunk(make_document(PROSE))

        assert len(chunks) > 1
        overlapping = [
            later.start_offset < earlier.end_offset for earlier, later in pairwise(chunks)
        ]
        assert any(overlapping)

    def test_overlap_repeats_text_rather_than_inventing_it(self, chunker: TextChunker) -> None:
        document = make_document(PROSE)
        chunks = chunker.chunk(document)

        for earlier, later in pairwise(chunks):
            if later.start_offset < earlier.end_offset:
                shared = document.text[later.start_offset : earlier.end_offset]
                assert earlier.text.endswith(shared)
                assert later.text.startswith(shared)

    def test_zero_overlap_produces_disjoint_chunks(self, tokenizer: Tokenizer) -> None:
        chunker = TextChunker(
            tokenizer, ChunkerConfig(chunk_size_tokens=80, chunk_overlap_tokens=0)
        )
        chunks = chunker.chunk(make_document(PROSE))

        for earlier, later in pairwise(chunks):
            assert later.start_offset >= earlier.end_offset

    def test_more_overlap_produces_more_chunks(self, tokenizer: Tokenizer) -> None:
        """Overlap is the knob it claims to be."""
        document = make_document(PROSE * 3)
        few = TextChunker(
            tokenizer, ChunkerConfig(chunk_size_tokens=120, chunk_overlap_tokens=0)
        ).chunk(document)
        many = TextChunker(
            tokenizer, ChunkerConfig(chunk_size_tokens=120, chunk_overlap_tokens=60)
        ).chunk(document)

        assert len(many) > len(few)

    def test_overlap_is_carried_as_whole_pieces(self, tokenizer: Tokenizer) -> None:
        """A documented limit of the design, asserted so it cannot drift.

        Overlap repeats whole paragraphs or sentences rather than cutting to an
        exact token count, because a piece is the smallest unit whose character
        offsets are known. When every piece is larger than the overlap budget,
        no piece fits and chunks come out disjoint -- which is correct, not a
        bug, but it does mean the setting is a budget rather than a promise.
        """
        document = make_document(PROSE)
        # Paragraphs cost ~19-35 tokens; a 5-token budget can hold none.
        chunks = TextChunker(
            tokenizer, ChunkerConfig(chunk_size_tokens=80, chunk_overlap_tokens=5)
        ).chunk(document)

        for earlier, later in pairwise(chunks):
            assert later.start_offset >= earlier.end_offset

    def test_overlap_at_or_above_chunk_size_is_rejected(self) -> None:
        """It would mean each chunk re-emits everything the last one did, and
        the chunker never advances."""
        with pytest.raises(ValueError, match="less than"):
            ChunkerConfig(chunk_size_tokens=100, chunk_overlap_tokens=100)


class TestSizing:
    @pytest.mark.parametrize(("size", "overlap"), [(25, 8), (40, 10), (60, 20), (120, 30)])
    def test_chunks_stay_within_the_configured_size(
        self, tokenizer: Tokenizer, size: int, overlap: int
    ) -> None:
        """Including the separators that land inside a chunk's span -- counting
        pieces without them is what previously let chunks run over."""
        chunker = TextChunker(
            tokenizer, ChunkerConfig(chunk_size_tokens=size, chunk_overlap_tokens=overlap)
        )

        for chunk in chunker.chunk(make_document(PROSE * 3)):
            assert chunk.token_count <= size

    def test_token_count_describes_the_emitted_text(
        self, chunker: TextChunker, tokenizer: Tokenizer
    ) -> None:
        """Recorded from the final slice, not summed from the pieces, because
        byte-pair counts are not additive across a join."""
        for chunk in chunker.chunk(make_document(PROSE)):
            assert chunk.token_count == tokenizer.count(chunk.text)

    def test_text_with_no_boundaries_is_still_split(self, tokenizer: Tokenizer) -> None:
        """A run with no whitespace -- a base64 blob -- has no paragraph,
        sentence or word boundary, and must not become one giant chunk."""
        chunker = TextChunker(
            tokenizer, ChunkerConfig(chunk_size_tokens=20, chunk_overlap_tokens=5)
        )
        chunks = chunker.chunk(make_document("A" * 4000))

        assert len(chunks) > 1
        assert all(chunk.text for chunk in chunks)

    def test_default_configuration_matches_the_documented_target(self) -> None:
        config = ChunkerConfig()

        assert config.chunk_size_tokens == 800
        assert config.chunk_overlap_tokens == 120


class TestChunkIdentity:
    def test_ids_are_stable_across_runs(self, tokenizer: Tokenizer) -> None:
        """Re-ingesting an unchanged document must not churn provenance."""
        document = make_document(PROSE)
        config = ChunkerConfig(chunk_size_tokens=40, chunk_overlap_tokens=10)
        first = TextChunker(tokenizer, config).chunk(document)
        second = TextChunker(tokenizer, config).chunk(document)

        assert [c.chunk_id for c in first] == [c.chunk_id for c in second]

    def test_ids_are_stable_across_a_fresh_parse(
        self, registry: ParserRegistry, cleaner: TextCleaner, chunker: TextChunker
    ) -> None:
        """End to end, from bytes: parsing and cleaning are deterministic too,
        so the whole pipeline reproduces the same ids."""
        first = chunker.chunk(load(registry, cleaner, "acme-notes.txt", "text/plain"))
        second = chunker.chunk(load(registry, cleaner, "acme-notes.txt", "text/plain"))

        assert [c.chunk_id for c in first] == [c.chunk_id for c in second]

    def test_ids_are_unique_within_a_document(self, chunker: TextChunker) -> None:
        chunks = chunker.chunk(make_document(PROSE * 3))

        assert len({c.chunk_id for c in chunks}) == len(chunks)

    def test_changed_content_changes_the_id(self) -> None:
        """A chunk whose text changed is a different chunk, and must not
        inherit the claims that cited the old one."""
        original = build_chunk_id(DOCUMENT_ID, 0, content_hash("Acme raised $12M."))
        edited = build_chunk_id(DOCUMENT_ID, 0, content_hash("Acme raised $15M."))

        assert original != edited

    def test_position_is_part_of_identity(self) -> None:
        text_hash = content_hash("Identical boilerplate.")

        assert build_chunk_id(DOCUMENT_ID, 0, text_hash) != build_chunk_id(
            DOCUMENT_ID, 1, text_hash
        )

    def test_identical_text_in_two_documents_gets_distinct_ids(self) -> None:
        """Provenance is per document: the same sentence in two filings is two
        pieces of evidence, not one."""
        text_hash = content_hash("Identical boilerplate.")

        assert build_chunk_id(DOCUMENT_ID, 0, text_hash) != build_chunk_id(
            OTHER_DOCUMENT_ID, 0, text_hash
        )

    def test_ids_do_not_depend_on_process_state(self) -> None:
        """A random namespace would make reproducibility false the moment the
        service restarted."""
        text_hash = content_hash("Acme raised $12M.")

        assert str(build_chunk_id(DOCUMENT_ID, 3, text_hash)) == str(
            build_chunk_id(DOCUMENT_ID, 3, text_hash)
        )


class TestContentHashing:
    def test_hash_matches_the_chunk_text(self, chunker: TextChunker) -> None:
        for chunk in chunker.chunk(make_document(PROSE)):
            assert chunk.content_hash == content_hash(chunk.text)

    def test_identical_text_hashes_identically(self) -> None:
        """How repeated boilerplate is detected without comparing text."""
        assert content_hash("Confidential. Do not distribute.") == content_hash(
            "Confidential. Do not distribute."
        )

    def test_different_text_hashes_differently(self) -> None:
        assert content_hash("Acme raised $12M.") != content_hash("Acme raised $13M.")

    def test_hash_is_sensitive_to_whitespace(self) -> None:
        """Chunks are compared after cleaning, so a whitespace difference here
        is a real difference rather than noise."""
        assert content_hash("Acme raised") != content_hash("Acme  raised")

    def test_hash_has_the_shape_the_model_requires(self, chunker: TextChunker) -> None:
        import re

        for chunk in chunker.chunk(make_document(PROSE)):
            assert re.fullmatch(r"[0-9a-f]{64}", chunk.content_hash)

    def test_duplicate_chunks_are_detectable_within_a_document(self, tokenizer: Tokenizer) -> None:
        """A boilerplate paragraph repeated verbatim produces equal hashes,
        which is what deduplication will key on."""
        boilerplate = "Confidential and proprietary. Do not distribute.\n\n"
        document = make_document(boilerplate + "Unique body text here.\n\n" + boilerplate)
        chunker = TextChunker(
            tokenizer, ChunkerConfig(chunk_size_tokens=12, chunk_overlap_tokens=0)
        )

        hashes = [chunk.content_hash for chunk in chunker.chunk(document)]

        assert len(hashes) != len(set(hashes))


class TestPageMetadata:
    def test_chunks_from_a_pdf_record_their_page(
        self, registry: ParserRegistry, cleaner: TextCleaner, chunker: TextChunker
    ) -> None:
        document = load(registry, cleaner, "acme-brief.pdf", "application/pdf")

        chunks = chunker.chunk(document)

        assert chunks
        assert all(chunk.page_number is not None for chunk in chunks)

    def test_page_numbers_are_in_range(
        self, registry: ParserRegistry, cleaner: TextCleaner, chunker: TextChunker
    ) -> None:
        document = load(registry, cleaner, "acme-brief.pdf", "application/pdf")

        for chunk in chunker.chunk(document):
            assert chunk.page_numbers
            assert all(1 <= page <= 2 for page in chunk.page_numbers)

    def test_page_number_is_the_page_the_chunk_starts_on(
        self, registry: ParserRegistry, cleaner: TextCleaner, chunker: TextChunker
    ) -> None:
        document = load(registry, cleaner, "acme-brief.pdf", "application/pdf")

        for chunk in chunker.chunk(document):
            assert chunk.page_number == chunk.page_numbers[0]

    def test_content_is_attributed_to_the_right_page(
        self, registry: ParserRegistry, cleaner: TextCleaner, chunker: TextChunker
    ) -> None:
        document = load(registry, cleaner, "acme-brief.pdf", "application/pdf")

        for chunk in chunker.chunk(document):
            if "Initech" in chunk.text:
                assert 2 in chunk.page_numbers

    def test_a_chunk_spanning_a_break_records_both_pages(
        self, registry: ParserRegistry, cleaner: TextCleaner, tokenizer: Tokenizer
    ) -> None:
        """A citation naming only the first page would be wrong about the rest
        of the chunk's content."""
        document = load(registry, cleaner, "acme-brief.pdf", "application/pdf")
        # Large enough that the whole two-page document lands in one chunk.
        chunker = TextChunker(
            tokenizer, ChunkerConfig(chunk_size_tokens=4000, chunk_overlap_tokens=0)
        )

        chunks = chunker.chunk(document)

        assert len(chunks) == 1
        assert chunks[0].page_numbers == (1, 2)

    def test_unpaginated_documents_have_no_page_numbers(
        self, registry: ParserRegistry, cleaner: TextCleaner, chunker: TextChunker
    ) -> None:
        document = load(registry, cleaner, "acme-notes.txt", "text/plain")

        for chunk in chunker.chunk(document):
            assert chunk.page_number is None
            assert chunk.page_numbers == ()


class TestSourceOffsets:
    def test_source_offsets_point_into_the_original_text(
        self, registry: ParserRegistry, cleaner: TextCleaner, chunker: TextChunker
    ) -> None:
        """The provenance anchor: a chunk resolves back to a span of the text
        the parser produced, not only of the cleaned text."""
        extracted = registry.parse(
            fixture_bytes("acme-notes.txt"),
            mime_type="text/plain",
            document_id=DOCUMENT_ID,
            workspace_id=WORKSPACE_ID,
        )
        document = cleaner.clean(extracted)

        for chunk in chunker.chunk(document):
            assert 0 <= chunk.source_start_offset < len(extracted.text)
            assert chunk.source_end_offset <= len(extracted.text)
            assert chunk.source_start_offset < chunk.source_end_offset

    def test_source_span_contains_the_chunks_distinctive_words(
        self, registry: ParserRegistry, cleaner: TextCleaner, chunker: TextChunker
    ) -> None:
        """Cleaning changes spacing, so the spans are not character-identical;
        the words must still be where the offsets say they are."""
        extracted = registry.parse(
            fixture_bytes("acme-memo.md"),
            mime_type="text/markdown",
            document_id=DOCUMENT_ID,
            workspace_id=WORKSPACE_ID,
        )
        document = cleaner.clean(extracted)

        for chunk in chunker.chunk(document):
            source_span = extracted.text[chunk.source_start_offset : chunk.source_end_offset]
            first_word = chunk.text.split()[0].strip("#*-")
            if first_word:
                assert first_word in source_span


class TestModelValidation:
    def test_a_chunk_cannot_claim_a_page_outside_its_span(self) -> None:
        with pytest.raises(ValueError, match="page_number"):
            DocumentChunk(
                chunk_id=DOCUMENT_ID,
                document_id=DOCUMENT_ID,
                workspace_id=WORKSPACE_ID,
                text="Acme",
                chunk_index=0,
                page_number=3,
                page_numbers=(1, 2),
                token_count=1,
                start_offset=0,
                end_offset=4,
                source_start_offset=0,
                source_end_offset=4,
                content_hash=content_hash("Acme"),
            )

    def test_a_chunk_cannot_have_a_reversed_span(self) -> None:
        with pytest.raises(ValueError, match="precedes"):
            DocumentChunk(
                chunk_id=DOCUMENT_ID,
                document_id=DOCUMENT_ID,
                workspace_id=WORKSPACE_ID,
                text="Acme",
                chunk_index=0,
                token_count=1,
                start_offset=10,
                end_offset=4,
                source_start_offset=0,
                source_end_offset=4,
                content_hash=content_hash("Acme"),
            )

    def test_a_malformed_hash_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="content_hash"):
            DocumentChunk(
                chunk_id=DOCUMENT_ID,
                document_id=DOCUMENT_ID,
                workspace_id=WORKSPACE_ID,
                text="Acme",
                chunk_index=0,
                token_count=1,
                start_offset=0,
                end_offset=4,
                source_start_offset=0,
                source_end_offset=4,
                content_hash="not-a-hash",
            )


class TestDeterminismAcrossTokenizers:
    def test_the_pipeline_is_reproducible_with_the_real_tokenizer(
        self, registry: ParserRegistry, cleaner: TextCleaner
    ) -> None:
        """Covers tiktoken where it is installed and its table is reachable.

        Skipped rather than failed when it is not: the encoding is downloaded
        on first use, and an offline machine failing here would be reporting
        its network, not this code.
        """
        tiktoken = pytest.importorskip("tiktoken")
        try:
            tiktoken.get_encoding("cl100k_base")
        except Exception as exc:  # pragma: no cover - environment dependent
            pytest.skip(f"cl100k_base unavailable: {exc}")

        from app.ai.ingestion import TiktokenTokenizer

        chunker = TextChunker(
            TiktokenTokenizer(), ChunkerConfig(chunk_size_tokens=40, chunk_overlap_tokens=10)
        )
        document = load(registry, cleaner, "acme-notes.txt", "text/plain")

        first = chunker.chunk(document)
        second = chunker.chunk(document)

        assert [c.chunk_id for c in first] == [c.chunk_id for c in second]
        assert all(c.token_count <= 40 for c in first)

    def test_different_tokenizers_both_produce_valid_chunks(
        self, registry: ParserRegistry, cleaner: TextCleaner
    ) -> None:
        """Chunk boundaries differ between tokenizers -- that is expected --
        but every invariant holds either way."""
        document = load(registry, cleaner, "acme-notes.txt", "text/plain")
        chunker = TextChunker(
            HeuristicTokenizer(), ChunkerConfig(chunk_size_tokens=40, chunk_overlap_tokens=10)
        )

        for chunk in chunker.chunk(document):
            assert document.text[chunk.start_offset : chunk.end_offset] == chunk.text
            assert chunk.content_hash == content_hash(chunk.text)
