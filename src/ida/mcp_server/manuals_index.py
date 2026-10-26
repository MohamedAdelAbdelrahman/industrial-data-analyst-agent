"""Manual retrieval: section-aware chunking, bge-small embeddings, Chroma vector store.

Build / rebuild the index:
    python -m ida.mcp_server.manuals_index
"""
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path

import pymupdf

from ida.manuals import Item, page_items

EMBED_MODEL = "BAAI/bge-small-en-v1.5"
QUERY_PREFIX = "Represent this sentence for searching relevant passages: "   # bge retrieval convention
COLLECTION = "manuals"



@dataclass
class Chunk:
    id: str
    doc: str            # file name
    machine_type: str
    page: int           # 1-based
    section: str
    text: str

    def embed_text(self) -> str:
        # prefix with doc + section so a short chunk still carries its context
        return f"{self.machine_type} manual. {self.section}\n{self.text}"


def machine_type_of(pdf: Path) -> str:
    with pymupdf.open(pdf) as doc:
        title = doc.metadata.get("title") or pdf.stem
    return title.split(":")[0].strip()


def chunk_pdf(pdf: Path) -> list[Chunk]:
    """One chunk per (section, page). A section continuing on the next page gets a second chunk."""
    mtype = machine_type_of(pdf)
    chunks: list[Chunk] = []
    section = "Introduction"
    with pymupdf.open(pdf) as doc:
        for page in doc:
            page_no = page.number + 1
            buf: list[str] = []
            # the trailing empty heading flushes the last section on the page (and keeps its name)
            for item in [*page_items(page), Item("heading", "")]:
                if item.kind != "heading":
                    buf.append(item.text)
                    continue
                body = "\n".join(buf).strip()
                if body:
                    chunks.append(Chunk(f"{pdf.stem}:p{page_no}:{len(chunks)}", pdf.name, mtype,
                                        page_no, section, body))
                buf = []
                section = item.text or section
    return chunks


class ManualIndex:
    def __init__(self, index_dir: Path, manuals_dir: Path):
        self.index_dir = index_dir
        self.manuals_dir = manuals_dir

    @cached_property
    def model(self):
        from sentence_transformers import SentenceTransformer
        try:    # skip the Hugging Face Hub update check once the model is cached (saves ~20 s)
            return SentenceTransformer(EMBED_MODEL, local_files_only=True)
        except (OSError, ValueError):
            return SentenceTransformer(EMBED_MODEL)

    def warm_up(self) -> None:
        """Load model + index now. Call from the main thread: on Windows, importing torch/scipy
        lazily inside an MCP worker thread can deadlock the stdio server."""
        self.collection.count()
        self.model.encode(["warm-up"])

    @cached_property
    def collection(self):
        import chromadb
        client = chromadb.PersistentClient(path=str(self.index_dir))
        col = client.get_or_create_collection(COLLECTION, metadata={"hnsw:space": "cosine"})
        if col.count() == 0:
            self._build(col)
        return col

    def _build(self, col) -> None:
        chunks = [c for pdf in sorted(self.manuals_dir.glob("*.pdf")) for c in chunk_pdf(pdf)]
        if not chunks:
            raise FileNotFoundError(f"No manuals in {self.manuals_dir} (run data/build_manuals.py)")
        emb = self.model.encode([c.embed_text() for c in chunks], normalize_embeddings=True)
        col.add(ids=[c.id for c in chunks], embeddings=emb.tolist(), documents=[c.text for c in chunks],
                metadatas=[{"doc": c.doc, "machine_type": c.machine_type, "page": c.page,
                            "section": c.section} for c in chunks])

    def machine_types(self) -> list[str]:
        return sorted({m["machine_type"] for m in self.collection.get(include=["metadatas"])["metadatas"]})

    def search(self, query: str, machine_type: str | None = None, k: int = 5) -> list[dict]:
        where = None
        if machine_type:
            match = {t.lower(): t for t in self.machine_types()}.get(machine_type.strip().lower())
            if match is None:
                raise ValueError(f"Unknown machine_type {machine_type!r}; use one of {self.machine_types()}")
            where = {"machine_type": match}
        q = self.model.encode([QUERY_PREFIX + query], normalize_embeddings=True)
        # over-fetch: sections shared by all manuals (safety, alarm response) are identical
        # text, so duplicates are folded into one hit that lists every doc containing it
        res = self.collection.query(query_embeddings=q.tolist(), n_results=min(4 * k, 50), where=where)
        hits: dict[str, dict] = {}
        for d, m, dist in zip(res["documents"][0], res["metadatas"][0], res["distances"][0]):
            if d in hits:
                hits[d]["also_in"].append(m["doc"])
            elif len(hits) < k:
                hits[d] = {"doc": m["doc"], "machine_type": m["machine_type"], "page": m["page"],
                           "section": m["section"], "text": d, "score": round(1 - dist, 3), "also_in": []}
        return list(hits.values())


def rebuild(index_dir: Path, manuals_dir: Path) -> int:
    import chromadb
    client = chromadb.PersistentClient(path=str(index_dir))
    if COLLECTION in [c.name for c in client.list_collections()]:
        client.delete_collection(COLLECTION)
    return ManualIndex(index_dir, manuals_dir).collection.count()


if __name__ == "__main__":
    from ida.config import get_settings
    s = get_settings()
    print(f"indexed {rebuild(s.index_dir, s.manuals_dir)} chunks into {s.index_dir}")
