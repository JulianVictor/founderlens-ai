# Test fixtures

Small sample documents for the ingestion tests, in `documents/`.

| File | Purpose |
| --- | --- |
| `acme-brief.pdf` | Two pages, so page attribution and multi-page chunks are testable. |
| `no-text-layer.pdf` | A valid PDF with no text — what a scan looks like to an extractor. |
| `acme-notes.txt` | Plain text with paragraphs and a non-ASCII character. |
| `acme-memo.md` | Markdown with YAML front matter, headings and a list. |
| `single-line.txt` | One line, no trailing newline. |
| `whitespace-only.txt` | Whitespace but no content. |
| `empty.txt` | Zero bytes. |

The PDFs are committed as binaries because generating them at test time would
make the tests depend on PyMuPDF producing byte-identical output across
versions. `generate_pdfs.py` records how they were made, so they can be
regenerated deliberately rather than reverse-engineered:

```bash
python backend/tests/fixtures/generate_pdfs.py
```
