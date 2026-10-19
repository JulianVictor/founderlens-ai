"""Whitespace normalisation, and the offset map that keeps it traceable."""

from __future__ import annotations

import pytest

from app.ai.ingestion import ParserRegistry, TextCleaner
from tests.ai.ingestion.conftest import DOCUMENT_ID, WORKSPACE_ID, fixture_bytes


class TestWhitespaceNormalisation:
    def test_collapses_repeated_spaces_inside_a_line(self, cleaner: TextCleaner) -> None:
        text, _ = cleaner.clean_text("Acme    raised   $12M")

        assert text == "Acme raised $12M"

    def test_collapses_tabs_inside_a_line_to_a_space(self, cleaner: TextCleaner) -> None:
        text, _ = cleaner.clean_text("Acme\t\traised")

        assert text == "Acme raised"

    def test_preserves_leading_indentation(self, cleaner: TextCleaner) -> None:
        """Markdown nesting and code blocks depend on it, so collapsing
        indentation would destroy structure the chunker relies on."""
        text, _ = cleaner.clean_text("- item\n    - nested item")

        assert text == "- item\n    - nested item"

    def test_strips_trailing_whitespace_from_lines(self, cleaner: TextCleaner) -> None:
        text, _ = cleaner.clean_text("Acme   \nraised\t\n")

        assert text == "Acme\nraised"

    def test_normalises_windows_line_endings(self, cleaner: TextCleaner) -> None:
        text, _ = cleaner.clean_text("Acme\r\nraised")

        assert text == "Acme\nraised"

    def test_normalises_a_lone_carriage_return(self, cleaner: TextCleaner) -> None:
        text, _ = cleaner.clean_text("Acme\rraised")

        assert text == "Acme\nraised"

    def test_keeps_one_blank_line_between_paragraphs(self, cleaner: TextCleaner) -> None:
        """Paragraph boundaries are what the chunker splits on, so they survive
        while the surplus page-gap noise does not."""
        text, _ = cleaner.clean_text("First.\n\n\n\n\nSecond.")

        assert text == "First.\n\nSecond."

    def test_leaves_a_single_blank_line_alone(self, cleaner: TextCleaner) -> None:
        text, _ = cleaner.clean_text("First.\n\nSecond.")

        assert text == "First.\n\nSecond."

    def test_trims_the_document(self, cleaner: TextCleaner) -> None:
        text, _ = cleaner.clean_text("\n\n  Acme raised $12M.  \n\n")

        assert text == "Acme raised $12M."

    def test_removes_invisible_characters(self, cleaner: TextCleaner) -> None:
        """Zero-width joiners and soft hyphens survive copy-paste and PDF
        extraction, and would otherwise break word matching invisibly."""
        text, _ = cleaner.clean_text("Ac­me​ Rob‍otics")

        assert text == "Acme Robotics"

    def test_rejoins_a_word_broken_across_a_line(self, cleaner: TextCleaner) -> None:
        """PDF extraction preserves typographic hyphenation, which would
        otherwise leave "automa" and "tion" as separate tokens."""
        text, _ = cleaner.clean_text("warehouse automa-\ntion hardware")

        assert text == "warehouse automation hardware"

    def test_does_not_rejoin_a_real_hyphenated_compound(self, cleaner: TextCleaner) -> None:
        """Only a hyphen immediately before a newline is typographic."""
        text, _ = cleaner.clean_text("mid-market logistics")

        assert text == "mid-market logistics"

    def test_is_idempotent(self, cleaner: TextCleaner) -> None:
        """Cleaning already-clean text must change nothing, or re-ingestion
        would not be reproducible."""
        once, _ = cleaner.clean_text("Acme   raised\r\n\n\n\n$12M.  ")
        twice, _ = cleaner.clean_text(once)

        assert twice == once

    def test_is_deterministic(self, cleaner: TextCleaner) -> None:
        source = fixture_bytes("acme-notes.txt").decode()

        first, first_map = cleaner.clean_text(source)
        second, second_map = cleaner.clean_text(source)

        assert first == second
        assert first_map == second_map

    def test_empty_input_produces_empty_output(self, cleaner: TextCleaner) -> None:
        text, offsets = cleaner.clean_text("")

        assert text == ""
        assert offsets.runs == ()

    def test_whitespace_only_input_cleans_to_nothing(self, cleaner: TextCleaner) -> None:
        text, _ = cleaner.clean_text("   \n\n\t\n  ")

        assert text == ""


class TestOffsetMap:
    @pytest.mark.parametrize(
        "source",
        [
            "Acme    raised   $12M",
            "\n\n  Acme raised.  \n\n",
            "Acme\r\nraised\r\n\r\n\r\nmore",
            "warehouse automa-\ntion hardware",
            "Ac­me Rob‍otics",
            "- item\n    - nested\n\n\n\npara",
            "plain text with nothing to clean",
        ],
    )
    def test_every_cleaned_character_maps_into_the_source(
        self, cleaner: TextCleaner, source: str
    ) -> None:
        """The property the whole design exists to guarantee.

        Each cleaned offset must resolve to a real source offset. An
        approximate map would be worse than none: a citation highlighting the
        wrong sentence is harder to notice than one that admits it cannot.
        """
        cleaned, offsets = cleaner.clean_text(source)

        for index in range(len(cleaned)):
            source_index = offsets.to_source(index)
            assert 0 <= source_index < len(source)

    def test_mapping_is_monotonic(self, cleaner: TextCleaner) -> None:
        """Reading order is preserved: later cleaned text came from later
        source text. Without this, a span could map to a reversed range."""
        source = "Acme   raised\r\n\n\n\n$12M   today.  "
        cleaned, offsets = cleaner.clean_text(source)

        mapped = [offsets.to_source(i) for i in range(len(cleaned))]
        assert mapped == sorted(mapped)

    def test_unchanged_text_maps_identically(self, cleaner: TextCleaner) -> None:
        source = "Acme raised $12M in March."
        cleaned, offsets = cleaner.clean_text(source)

        assert cleaned == source
        assert all(offsets.to_source(i) == i for i in range(len(cleaned)))

    def test_characters_correspond_across_the_map(self, cleaner: TextCleaner) -> None:
        """Beyond offsets being in range, they point at the right character.

        Checked only for non-whitespace, because whitespace is exactly what
        cleaning substitutes.
        """
        source = "Acme    raised\r\n\n\n\n$12M in automa-\ntion."
        cleaned, offsets = cleaner.clean_text(source)

        for index, character in enumerate(cleaned):
            if character.isspace():
                continue
            assert source[offsets.to_source(index)] == character

    def test_offset_past_the_end_resolves_to_the_end(self, cleaner: TextCleaner) -> None:
        """A half-open cleaned span must convert to a half-open source span,
        so the end offset has to be mappable."""
        source = "Acme raised."
        cleaned, offsets = cleaner.clean_text(source)

        assert offsets.to_source(len(cleaned)) == len(source)

    def test_a_negative_offset_is_rejected(self, cleaner: TextCleaner) -> None:
        _, offsets = cleaner.clean_text("Acme")

        with pytest.raises(ValueError, match="negative"):
            offsets.to_source(-1)

    def test_runs_are_merged_rather_than_one_per_edit(self, cleaner: TextCleaner) -> None:
        """Without merging, the map would grow to roughly the size of the
        document it describes."""
        _, offsets = cleaner.clean_text("Acme raised $12M with nothing to clean.")

        assert len(offsets.runs) == 1


class TestCleanDocument:
    def test_carries_pages_through(self, registry: ParserRegistry, cleaner: TextCleaner) -> None:
        extracted = registry.parse(
            fixture_bytes("acme-brief.pdf"),
            mime_type="application/pdf",
            document_id=DOCUMENT_ID,
            workspace_id=WORKSPACE_ID,
        )

        cleaned = cleaner.clean(extracted)

        assert len(cleaned.pages) == extracted.page_count
        assert cleaned.document_id == DOCUMENT_ID
        assert cleaned.workspace_id == WORKSPACE_ID

    def test_page_lookup_works_through_cleaned_offsets(
        self, registry: ParserRegistry, cleaner: TextCleaner
    ) -> None:
        """Page attribution has to survive cleaning, or a chunk cut from
        cleaned text could not name the page it came from."""
        extracted = registry.parse(
            fixture_bytes("acme-brief.pdf"),
            mime_type="application/pdf",
            document_id=DOCUMENT_ID,
            workspace_id=WORKSPACE_ID,
        )
        cleaned = cleaner.clean(extracted)

        assert cleaned.page_for_clean_offset(0) == 1
        assert cleaned.page_for_clean_offset(len(cleaned.text) - 1) == 2

    def test_unpaginated_documents_report_no_page(
        self, registry: ParserRegistry, cleaner: TextCleaner
    ) -> None:
        extracted = registry.parse(
            fixture_bytes("acme-notes.txt"),
            mime_type="text/plain",
            document_id=DOCUMENT_ID,
            workspace_id=WORKSPACE_ID,
        )
        cleaned = cleaner.clean(extracted)

        assert cleaned.page_for_clean_offset(0) is None
