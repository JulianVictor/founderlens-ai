"""Content sniffing: deciding what a file is from its bytes."""

from __future__ import annotations

import pytest

from app.core.content_detection import (
    DocumentType,
    detect_document_type,
    looks_like_pdf,
    looks_like_text,
    sanitise_filename,
    sha256_hex,
)
from tests.conftest import MARKDOWN_BYTES, PDF_BYTES, PNG_BYTES, TEXT_BYTES


class TestPdfDetection:
    def test_recognises_a_pdf(self) -> None:
        assert looks_like_pdf(PDF_BYTES)

    def test_tolerates_a_few_leading_bytes(self) -> None:
        """Real PDFs from real tools sometimes carry a preamble."""
        assert looks_like_pdf(b"\n\n" + PDF_BYTES)

    def test_does_not_scan_the_whole_file(self) -> None:
        """A PDF header buried past the window is not a PDF.

        Otherwise any file that merely *contains* the string -- including a text
        file discussing PDFs -- would be classified as one.
        """
        assert not looks_like_pdf(b"x" * 2048 + PDF_BYTES)

    def test_text_is_not_a_pdf(self) -> None:
        assert not looks_like_pdf(TEXT_BYTES)


class TestTextDetection:
    def test_recognises_utf8_text(self) -> None:
        assert looks_like_text(TEXT_BYTES)

    def test_recognises_non_ascii_text(self) -> None:
        assert looks_like_text("Voilà — naïve café 日本語\n".encode())

    def test_rejects_content_with_nul_bytes(self) -> None:
        """A NUL byte is how binary content announces itself."""
        assert not looks_like_text(b"hello\x00world")

    def test_rejects_invalid_utf8(self) -> None:
        assert not looks_like_text(b"\xff\xfe\xfd\xfc" * 10)

    def test_tolerates_a_character_split_by_the_sample_boundary(self) -> None:
        """Only the prefix is inspected, so the last character may be cut in
        half. That is a fact about the slice, not about the file."""
        # 8191 ASCII bytes then a 3-byte character, so the sample ends mid-char.
        data = b"a" * 8191 + "日".encode()
        assert looks_like_text(data)


class TestDetectDocumentType:
    def test_pdf_bytes_are_a_pdf(self) -> None:
        assert detect_document_type(PDF_BYTES, "memo.pdf") is DocumentType.PDF

    def test_text_bytes_are_plain_text(self) -> None:
        assert detect_document_type(TEXT_BYTES, "notes.txt") is DocumentType.PLAIN_TEXT

    @pytest.mark.parametrize("filename", ["notes.md", "notes.markdown", "NOTES.MD", "a.mkd"])
    def test_markdown_extension_selects_markdown(self, filename: str) -> None:
        """The extension picks between the two *text* types.

        This is the one thing it is allowed to decide, because both types are
        stored and parsed identically -- lying here gains nothing.
        """
        assert detect_document_type(MARKDOWN_BYTES, filename) is DocumentType.MARKDOWN

    def test_empty_content_is_not_a_type(self) -> None:
        assert detect_document_type(b"", "empty.txt") is None

    def test_binary_is_rejected(self) -> None:
        assert detect_document_type(PNG_BYTES, "image.png") is None

    # --- The point of the whole module --------------------------------------

    def test_a_pdf_extension_does_not_make_binary_into_a_pdf(self) -> None:
        """The attack this module exists to stop.

        A PNG renamed to .pdf must be refused, because the ingestion pipeline
        would otherwise hand arbitrary bytes to a PDF parser.
        """
        assert detect_document_type(PNG_BYTES, "totally-a-report.pdf") is None

    def test_a_txt_extension_does_not_make_binary_into_text(self) -> None:
        assert detect_document_type(PNG_BYTES, "notes.txt") is None

    def test_a_real_pdf_is_a_pdf_whatever_it_is_called(self) -> None:
        """Detection runs on content, so a misleading name cannot downgrade a
        file either."""
        assert detect_document_type(PDF_BYTES, "notes.txt") is DocumentType.PDF
        assert detect_document_type(PDF_BYTES, "no-extension") is DocumentType.PDF

    def test_an_executable_is_refused_however_it_is_named(self) -> None:
        elf = b"\x7fELF\x02\x01\x01\x00" + b"\x00" * 64
        for filename in ("payload.pdf", "payload.txt", "payload.md"):
            assert detect_document_type(elf, filename) is None


class TestStoragePathExtension:
    def test_extension_comes_from_the_detected_type(self) -> None:
        assert DocumentType.PDF.extension == "pdf"
        assert DocumentType.PLAIN_TEXT.extension == "txt"
        assert DocumentType.MARKDOWN.extension == "md"

    def test_every_type_has_an_extension(self) -> None:
        """A type added without an extension would raise mid-upload."""
        for document_type in DocumentType:
            assert document_type.extension


class TestSanitiseFilename:
    def test_keeps_an_ordinary_name(self) -> None:
        assert sanitise_filename("acme-memo.pdf") == "acme-memo.pdf"

    @pytest.mark.parametrize(
        ("given", "expected"),
        [
            ("../../etc/passwd", "passwd"),
            ("..\\..\\windows\\system32", "system32"),
            ("/absolute/path/report.pdf", "report.pdf"),
            ("C:\\Users\\ada\\report.pdf", "report.pdf"),
        ],
    )
    def test_strips_directory_components(self, given: str, expected: str) -> None:
        assert sanitise_filename(given) == expected

    def test_strips_control_characters(self) -> None:
        assert sanitise_filename("re\x00po\x1brt.pdf") == "report.pdf"

    @pytest.mark.parametrize("given", ["", "   ", "...", "..", "/", "\x00"])
    def test_falls_back_when_nothing_usable_survives(self, given: str) -> None:
        """documents.filename is NOT NULL and rejects blanks, so sanitising must
        never produce something the database would refuse."""
        assert sanitise_filename(given) == "untitled"

    def test_truncates_to_the_column_limit(self) -> None:
        assert len(sanitise_filename("a" * 500)) == 255


class TestChecksum:
    def test_is_stable(self) -> None:
        assert sha256_hex(TEXT_BYTES) == sha256_hex(TEXT_BYTES)

    def test_differs_for_different_content(self) -> None:
        assert sha256_hex(TEXT_BYTES) != sha256_hex(MARKDOWN_BYTES)

    def test_matches_the_database_constraint(self) -> None:
        """documents.checksum_sha256 is checked against ^[0-9a-f]{64}$."""
        import re

        assert re.fullmatch(r"[0-9a-f]{64}", sha256_hex(TEXT_BYTES))
