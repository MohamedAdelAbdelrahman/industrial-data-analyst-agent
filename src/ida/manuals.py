"""PDF text extraction for the manuals corpus (used by search_manuals ingestion)."""
import re
import unicodedata
from pathlib import Path

import pymupdf

# Header/footer lines added by data/build_manuals.py; they carry no content.
_BOILERPLATE = re.compile(r"^(Page \d+ of \d+|.+: Operation and Maintenance Manual)$")


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)      # ligatures (U+FB01 'fi') -> plain letters
    text = re.sub(r"-\n(?=\S)", "-", text)          # keep hyphenated codes intact: 'SP-CNC-\nBRG' -> 'SP-CNC-BRG'
    text = re.sub(r"[ \t]*\n[ \t]*", "\n", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def extract_pages(path: str | Path) -> list[str]:
    """Return the normalized text of each page (index 0 = page 1)."""
    pages = []
    with pymupdf.open(path) as doc:
        for page in doc:
            lines = [ln for ln in page.get_text().splitlines() if not _BOILERPLATE.match(ln.strip())]
            pages.append(normalize("\n".join(lines)))
    return pages
