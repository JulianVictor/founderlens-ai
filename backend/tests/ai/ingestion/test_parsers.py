"""Parsing PDF, plain text and Markdown into text with provenance."""

from __future__ import annotations

import pytest

from app.ai.ingestion import (
    PAGE_SEPARATOR,
    ExtractedDocument,
    MarkdownParser,
    ParserRegistry,
    PDFParser,
    TextParser,
)
from app.core.content_detection import DocumentType
from app.core.exceptions import DocumentParseError
from tests.ai.ingestion.conftest import DOCUMENT_ID, WORKSPACE_ID, fixture_bytes


def parse(parser: object, name: str) -> ExtractedDocument:
    return parser.parse(  # type: ignore[attr-defined,no-any-return]
        fixture_bytes(name), document_id=DOCUMENT_ID, workspace_id=WORKSPACE_ID
    )


class TestPDFExtraction:
    def test_extracts_text_from_every_page(self) -> None:
        document = parse(PDFParser(), "acme-brief.pdf")

        assert document.page_count == 2
        assert "Acme Robotics" in document.text
        assert "Initech Ventures" in document.text

    def test_page_numbers_are_one_based_and_sequential(self) -> None:
        document = parse(PDFParser(), "acme-brief.pdf")

        assert [page.page_number for page in document.pages] == [1, 2]

    def test_page_spans_index_the_document_text_exactly(self) -> None:
        """The invariant every page citation rests on."""
        document = parse(PDFParser(), "acme-brief.pdf")

        for page in document.pages:
            assert document.text[page.start_offset : page.end_offset] == page.text

    def test_pages_are_joined_by_the_declared_separator(self) -> None:
        """Nothing else is inserted, so page offsets index the joined text."""
        document = parse(PDFParser(), "acme-brief.pdf")
        first, second = document.pages

        between = document.text[first.end_offset : second.start_offset]
        assert between == PAGE_SEPARATOR

    def test_content_is_attributed_to_the_page_it_came_from(self) -> None:
        document = parse(PDFParser(), "acme-brief.pdf")

        assert "Funding" not in document.pages[0].text
        assert "Funding" in document.pages[1].text

    def test_page_lookup_resolves_an_offset_to_its_page(self) -> None:
        document = parse(PDFParser(), "acme-brief.pdf")
        second = document.pages[1]

        assert document.page_for_offset(document.pages[0].start_offset) == 1
        assert document.page_for_offset(second.start_offset) == 2

    def test_offset_in_a_page_separator_belongs_to_no_page(self) -> None:
        """Not an error: the separator is document text that is on no page."""
        document = parse(PDFParser(), "acme-brief.pdf")

        assert document.page_for_offset(document.pages[0].end_offset) is None

    def test_preserves_document_metadata(self) -> None:
        document = parse(PDFParser(), "acme-brief.pdf")

        assert document.metadata["title"] == "Acme Robotics Brief"
        assert document.metadata["author"] == "Research Desk"
        assert document.metadata["page_count"] == "2"

    def test_records_the_detected_type(self) -> None:
        document = parse(PDFParser(), "acme-brief.pdf")

        assert document.mime_type == DocumentType.PDF.value
        assert document.is_paginated

    def test_a_pdf_with_no_text_layer_is_reported_as_such(self) -> None:
        """A scan is a real document we cannot read, and saying so points at
        the fix rather than leaving an empty result to be puzzled over."""
        with pytest.raises(DocumentParseError, match="OCR"):
            parse(PDFParser(), "no-text-layer.pdf")

    def test_bytes_that_are_not_a_pdf_are_refused(self) -> None:
        with pytest.raises(DocumentParseError, match="readable PDF"):
            PDFParser().parse(
                b"this is not a pdf at all",
                document_id=DOCUMENT_ID,
                workspace_id=WORKSPACE_ID,
            )

    def test_truncated_pdf_is_refused(self) -> None:
        truncated = fixture_bytes("acme-brief.pdf")[:200]

        with pytest.raises(DocumentParseError):
            PDFParser().parse(truncated, document_id=DOCUMENT_ID, workspace_id=WORKSPACE_ID)


class TestTextParsing:
    def test_extracts_the_whole_file(self) -> None:
        document = parse(TextParser(), "acme-notes.txt")

        assert "Acme Robotics" in document.text
        assert "time-to-value" in document.text

    def test_plain_text_has_no_pages(self) -> None:
        document = parse(TextParser(), "acme-notes.txt")

        assert document.pages == ()
        assert not document.is_paginated
        assert document.page_for_offset(0) is None

    def test_text_is_left_unnormalised(self) -> None:
        """Normalisation belongs to the cleaner.

        If the parser normalised, source offsets would already index a
        transformation of the file and provenance would start one step away
        from the original.
        """
        raw = fixture_bytes("acme-notes.txt").decode("utf-8")
        document = parse(TextParser(), "acme-notes.txt")

        assert document.text == raw

    def test_non_ascii_survives(self) -> None:
        document = parse(TextParser(), "acme-notes.txt")

        assert "—" in document.text  # em dash

    def test_a_file_without_a_trailing_newline_is_fine(self) -> None:
        document = parse(TextParser(), "single-line.txt")

        assert document.text == "One line, no trailing newline."

    def test_strips_a_byte_order_mark(self) -> None:
        """A BOM would otherwise become a character at offset 0 of every
        document written by an editor that emits one."""
        document = TextParser().parse(
            "﻿Acme".encode(), document_id=DOCUMENT_ID, workspace_id=WORKSPACE_ID
        )

        assert document.text == "Acme"

    def test_invalid_utf8_is_refused_rather_than_mangled(self) -> None:
        """The upload check only sampled the first 8 KB, so a file can reach
        here and still be undecodable. Replacing bad bytes would silently
        corrupt text a citation later quotes.
        """
        with pytest.raises(DocumentParseError, match="UTF-8"):
            TextParser().parse(
                b"valid prefix " + b"\xff\xfe\xfd",
                document_id=DOCUMENT_ID,
                workspace_id=WORKSPACE_ID,
            )


class TestMarkdownParsing:
    def test_extracts_the_body(self) -> None:
        document = parse(MarkdownParser(), "acme-memo.md")

        assert "Acme Robotics sells warehouse automation" in document.text

    def test_keeps_the_markup(self) -> None:
        """Headings and list markers are structure the chunker splits on, and
        they read correctly when a citation is displayed."""
        document = parse(MarkdownParser(), "acme-memo.md")

        assert "# Acme Robotics" in document.text
        assert "## Funding" in document.text
        assert "- Ada Okonkwo" in document.text

    def test_front_matter_is_removed_from_the_body(self) -> None:
        """Left in, `title: ...` would land in the first chunk as though the
        author had written it as prose."""
        document = parse(MarkdownParser(), "acme-memo.md")

        assert "status: draft" not in document.text
        # The blank line the block left behind is not stripped here: the parser
        # removes the block and nothing else, and the cleaner trims the rest.
        assert document.text.lstrip().startswith("# Acme Robotics")

    def test_front_matter_is_kept_as_metadata(self) -> None:
        document = parse(MarkdownParser(), "acme-memo.md")

        assert document.metadata["frontmatter_title"] == "Acme Robotics Investment Memo"
        assert document.metadata["frontmatter_author"] == "Research Desk"

    def test_records_the_heading_outline(self) -> None:
        document = parse(MarkdownParser(), "acme-memo.md")

        assert document.metadata["heading_count"] == "5"
        assert "Funding" in document.metadata["headings"]

    def test_markdown_without_front_matter_is_unchanged(self) -> None:
        body = b"# Title\n\nSome prose.\n"
        document = MarkdownParser().parse(body, document_id=DOCUMENT_ID, workspace_id=WORKSPACE_ID)

        assert document.text == body.decode()
        assert not any(key.startswith("frontmatter_") for key in document.metadata)

    def test_a_horizontal_rule_is_not_mistaken_for_front_matter(self) -> None:
        """`---` only opens front matter at the very start of the file."""
        body = b"# Title\n\n---\n\nAfter the rule.\n"
        document = MarkdownParser().parse(body, document_id=DOCUMENT_ID, workspace_id=WORKSPACE_ID)

        assert "After the rule." in document.text
        assert document.text.startswith("# Title")


class TestEmptyDocuments:
    @pytest.mark.parametrize(
        "parser", [PDFParser(), TextParser(), MarkdownParser()], ids=lambda p: type(p).__name__
    )
    def test_zero_bytes_is_refused(self, parser: object) -> None:
        with pytest.raises(DocumentParseError, match="empty"):
            parser.parse(  # type: ignore[attr-defined]
                b"", document_id=DOCUMENT_ID, workspace_id=WORKSPACE_ID
            )

    @pytest.mark.parametrize(
        "parser", [TextParser(), MarkdownParser()], ids=lambda p: type(p).__name__
    )
    def test_whitespace_only_is_refused(self, parser: object) -> None:
        """A file of blank lines has no content to chunk, embed or cite, and
        accepting it would produce a document that can never answer anything.
        """
        with pytest.raises(DocumentParseError, match="no text"):
            parser.parse(  # type: ignore[attr-defined]
                fixture_bytes("whitespace-only.txt"),
                document_id=DOCUMENT_ID,
                workspace_id=WORKSPACE_ID,
            )


class TestRegistry:
    def test_selects_a_parser_by_content_type(self, registry: ParserRegistry) -> None:
        document = registry.parse(
            fixture_bytes("acme-brief.pdf"),
            mime_type="application/pdf",
            document_id=DOCUMENT_ID,
            workspace_id=WORKSPACE_ID,
        )

        assert document.page_count == 2

    def test_covers_every_type_the_upload_path_accepts(self, registry: ParserRegistry) -> None:
        """A type accepted at upload but unparseable here would be a document
        that can be stored and never ingested."""
        assert registry.supported_types == frozenset(DocumentType)

    def test_markdown_and_plain_text_get_different_parsers(self, registry: ParserRegistry) -> None:
        assert registry.for_type(DocumentType.MARKDOWN) is not registry.for_type(
            DocumentType.PLAIN_TEXT
        )

    def test_an_unknown_content_type_is_a_parse_error(self, registry: ParserRegistry) -> None:
        with pytest.raises(DocumentParseError, match="no parser"):
            registry.parse(
                b"data",
                mime_type="application/vnd.ms-excel",
                document_id=DOCUMENT_ID,
                workspace_id=WORKSPACE_ID,
            )

    def test_two_parsers_for_one_type_is_rejected_at_construction(self) -> None:
        """Ambiguous registration is a bug, and a silent winner would make the
        behaviour depend on list order."""
        with pytest.raises(ValueError, match="Two parsers"):
            ParserRegistry([TextParser(), TextParser()])

    def test_the_document_carries_its_identifiers(self, registry: ParserRegistry) -> None:
        document = registry.parse(
            fixture_bytes("acme-notes.txt"),
            mime_type="text/plain",
            document_id=DOCUMENT_ID,
            workspace_id=WORKSPACE_ID,
        )

        assert document.document_id == DOCUMENT_ID
        assert document.workspace_id == WORKSPACE_ID
