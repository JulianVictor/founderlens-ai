"""Determining what an uploaded file actually is.

The rule this module exists to enforce: **the content type is decided by the
bytes, not by the client**. A filename extension and a multipart ``Content-Type``
header are both attacker-controlled strings. Storing a file as ``application/pdf``
because it was called ``report.pdf`` means the ingestion pipeline will later hand
arbitrary bytes to a PDF parser, and means a browser asked to render it may be
told to treat it as something it is not.

What is actually checkable
--------------------------
PDF has a magic number, so "is this a PDF" is a real question with a real
answer. Plain text and Markdown do not -- a ``.md`` file and a ``.txt`` file are
the same bytes -- so the honest check is "is this decodable text", and that is
what is enforced.

The extension is consulted for exactly one thing: choosing between ``text/plain``
and ``text/markdown`` once the content has already been proven to be text. That
choice is cosmetic -- both are stored identically and both will be chunked as
text -- so a lie there buys an attacker nothing. The extension is never allowed
to promote a file into a type the bytes do not support.
"""

from __future__ import annotations

import hashlib
from enum import StrEnum
from pathlib import PurePosixPath

#: Bytes at the start of every PDF.
_PDF_MAGIC = b"%PDF-"

#: How far into the file to look for the PDF header. The specification puts it
#: at offset zero, but real files from real tools sometimes carry a few leading
#: bytes, and readers tolerate it. A bounded window tolerates the same without
#: turning the check into "contains %PDF- somewhere".
_PDF_MAGIC_WINDOW = 1024

#: How much of a text file to inspect. Decoding the whole of a large upload to
#: answer a yes/no question is wasted work; a prefix is enough to tell text from
#: binary, and the ingestion pipeline decodes the rest properly later.
_TEXT_SNIFF_BYTES = 8192

_MARKDOWN_SUFFIXES = frozenset({".md", ".markdown", ".mdown", ".mkd"})


class DocumentType(StrEnum):
    """A content type FounderLens accepts. The value is the stored MIME type."""

    PDF = "application/pdf"
    PLAIN_TEXT = "text/plain"
    MARKDOWN = "text/markdown"

    @property
    def extension(self) -> str:
        """Suffix used when building the storage key.

        Server-chosen, from the detected type -- never taken from the upload.
        """
        return _EXTENSIONS[self]

    @property
    def is_text(self) -> bool:
        return self is not DocumentType.PDF


_EXTENSIONS: dict[DocumentType, str] = {
    DocumentType.PDF: "pdf",
    DocumentType.PLAIN_TEXT: "txt",
    DocumentType.MARKDOWN: "md",
}


def looks_like_pdf(data: bytes) -> bool:
    """Whether ``data`` carries a PDF header within the leading window."""
    return _PDF_MAGIC in data[:_PDF_MAGIC_WINDOW]


def looks_like_text(data: bytes) -> bool:
    """Whether ``data`` is plausibly UTF-8 text rather than binary.

    A NUL byte is the giveaway for binary content: it cannot appear in a text
    file that any of our downstream tooling would accept, and it is how
    executables, images and archives announce themselves early.
    """
    sample = data[:_TEXT_SNIFF_BYTES]
    if b"\x00" in sample:
        return False

    try:
        sample.decode("utf-8")
    except UnicodeDecodeError as exc:
        # A truncated multi-byte character at the sample boundary is not a
        # decoding failure of the file, only of the slice. Anything that fails
        # earlier than the last few bytes is genuinely not UTF-8.
        return exc.start >= len(sample) - 4

    return True


def detect_document_type(data: bytes, filename: str) -> DocumentType | None:
    """Identify ``data``, or return ``None`` if it is not an accepted type.

    ``filename`` is used only to pick between the two text types, and only after
    the content has been shown to be text.
    """
    if not data:
        return None

    if looks_like_pdf(data):
        return DocumentType.PDF

    if looks_like_text(data):
        suffix = PurePosixPath(filename).suffix.lower()
        return DocumentType.MARKDOWN if suffix in _MARKDOWN_SUFFIXES else DocumentType.PLAIN_TEXT

    return None


def sha256_hex(data: bytes) -> str:
    """Content digest, used as the dedup key and the provenance anchor."""
    return hashlib.sha256(data).hexdigest()


def sanitise_filename(filename: str, *, max_length: int = 255) -> str:
    """Reduce a client-supplied filename to something safe to store and show.

    This value is *display only* -- it never becomes part of a storage key or a
    filesystem path. It is sanitised anyway because it is rendered in a UI and
    logged, and because a name carrying directory separators or control
    characters is a lie about what the file is regardless of where it is used.

    Returns ``"untitled"`` when nothing usable survives, so that the NOT NULL,
    non-blank constraint on ``documents.filename`` can never be the thing that
    rejects an otherwise valid upload.
    """
    # Take the last path segment under both separators: a browser on Windows can
    # submit a full path, and "..\\..\\etc" must not survive as a name.
    candidate = filename.replace("\\", "/").split("/")[-1]
    candidate = "".join(character for character in candidate if character.isprintable()).strip()
    # A name that is only dots reads as a relative path everywhere it is shown.
    if not candidate or set(candidate) <= {"."}:
        return "untitled"
    return candidate[:max_length]
