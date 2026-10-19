"""Regenerate the PDF fixtures.

Not run by the test suite. The PDFs are committed so that tests do not depend
on PyMuPDF emitting byte-identical files across versions; this script exists so
that regenerating them is a deliberate, reviewable act rather than guesswork.

    python backend/tests/fixtures/generate_pdfs.py
"""

from __future__ import annotations

import pathlib

import pymupdf

OUTPUT = pathlib.Path(__file__).parent / "documents"


def build_brief() -> None:
    """Two pages of extractable text, with a heading on each."""
    document = pymupdf.open()

    first = document.new_page()
    first.insert_text((72, 100), "Acme Robotics", fontsize=18)
    first.insert_text((72, 140), "Acme Robotics builds warehouse automation", fontsize=11)
    first.insert_text((72, 158), "hardware for mid-market logistics operators.", fontsize=11)
    first.insert_text((72, 176), "Founded in 2021 by Ada Okonkwo and Grace Lin.", fontsize=11)

    second = document.new_page()
    second.insert_text((72, 100), "Funding", fontsize=18)
    second.insert_text((72, 140), "The Series A closed in March 2026 at $12M,", fontsize=11)
    second.insert_text((72, 158), "led by Initech Ventures. Seed investors", fontsize=11)
    second.insert_text((72, 176), "participated in the round.", fontsize=11)

    document.set_metadata({"title": "Acme Robotics Brief", "author": "Research Desk"})
    document.save(OUTPUT / "acme-brief.pdf")
    document.close()


def build_no_text_layer() -> None:
    """A structurally valid PDF with nothing extractable, like a scan."""
    document = pymupdf.open()
    document.new_page()
    document.save(OUTPUT / "no-text-layer.pdf")
    document.close()


if __name__ == "__main__":
    build_brief()
    build_no_text_layer()
    for path in sorted(OUTPUT.glob("*.pdf")):
        print(f"{path.name}: {path.stat().st_size} bytes")
