"""PDF text extraction for the manuals corpus (used by search_manuals ingestion).

Pages are read as an ordered list of items: headings (detected by font, not by text
pattern, since numbered list steps look like '1. ...' too), body lines, and tables
(rendered one row per line as 'Header: value | ...' so a row stays together in a chunk).
"""
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

import pymupdf

# Header/footer lines added by data/build_manuals.py; they carry no content.
_BOILERPLATE = re.compile(r"^(Page \d+ of \d+|.+: Operation and Maintenance Manual)$")
HEADING_MIN_SIZE = 10.5     # body text is 10 pt; section headings are bold 11-13 pt
TITLE_MIN_SIZE = 16


@dataclass
class Item:
    kind: str       # 'heading' | 'text' | 'table'
    text: str


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)      # ligatures (U+FB01 'fi') -> plain letters
    text = re.sub(r"-\n(?=\S)", "-", text)          # keep hyphenated codes intact: 'SP-CNC-\nBRG' -> 'SP-CNC-BRG'
    text = re.sub(r"[ \t]*\n[ \t]*", "\n", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _cell(page: pymupdf.Page, bbox) -> str:
    # table.extract() drops spaces between words in these PDFs; read the cell area instead
    text = page.get_text("text", clip=pymupdf.Rect(bbox)) if bbox else ""
    return re.sub(r"\s+", " ", normalize(text)).replace("HIGH_ HIGH", "HIGH_HIGH").strip()


def _table_text(page: pymupdf.Page, table) -> str:
    rows = [[_cell(page, c) for c in row.cells] for row in table.rows]
    header = rows[0]
    return "\n".join(" | ".join(f"{h}: {v}" for h, v in zip(header, row)) for row in rows[1:])


def page_items(page: pymupdf.Page) -> list[Item]:
    tables = page.find_tables().tables
    boxes = [pymupdf.Rect(t.bbox) for t in tables]
    placed: list[tuple[float, Item]] = [(b.y0, Item("table", _table_text(page, t))) for b, t in zip(boxes, tables)]

    for block in page.get_text("dict")["blocks"]:
        for line in block.get("lines", []):
            spans = [s for s in line["spans"] if s["text"].strip()]
            if not spans:
                continue
            bbox = pymupdf.Rect(line["bbox"])
            if any(bbox.intersects(b) for b in boxes):
                continue
            text = "".join(s["text"] for s in spans).strip()
            if _BOILERPLATE.match(text):
                continue
            size = max(s["size"] for s in spans)
            bold = all(s["flags"] & 16 for s in spans)
            if bold and HEADING_MIN_SIZE <= size < TITLE_MIN_SIZE:
                placed.append((bbox.y0, Item("heading", normalize(text))))
            else:
                placed.append((bbox.y0, Item("text", text)))

    placed.sort(key=lambda p: p[0])
    items: list[Item] = []
    for _, item in placed:     # merge consecutive body lines, then normalize (fixes split part numbers)
        if item.kind == "text" and items and items[-1].kind == "text":
            items[-1].text += "\n" + item.text
        else:
            items.append(item)
    for item in items:
        if item.kind == "text":
            item.text = normalize(item.text)
    return items


def extract_pages(path: str | Path) -> list[str]:
    """Return the normalized text of each page (index 0 = page 1)."""
    with pymupdf.open(path) as doc:
        return ["\n".join(i.text for i in page_items(page)) for page in doc]
