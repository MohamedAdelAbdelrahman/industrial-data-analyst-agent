"""search_manuals: section-aware chunking, filtering, de-duplication and retrieval quality.

Uses the real embedding model (local, no API) and the committed PDFs in data/manuals.
"""
import sys
from pathlib import Path

import pytest

from ida.mcp_server.manuals_index import ManualIndex, chunk_pdf

ROOT = Path(__file__).resolve().parents[1]
MANUALS = ROOT / "data" / "manuals"
sys.path.insert(0, str(ROOT / "eval"))
from eval_retrieval import evaluate


@pytest.fixture(scope="module")
def index(tmp_path_factory):
    return ManualIndex(tmp_path_factory.mktemp("index"), MANUALS)


def test_chunks_follow_sections_and_pages():
    chunks = chunk_pdf(MANUALS / "pump_manual.pdf")
    assert {c.machine_type for c in chunks} == {"Pump"}
    sections = [c.section for c in chunks]
    for expected in ["1. Safety", "2. Sensors and alarm thresholds", "3. Alarm response",
                     "4.3 High vibration (bearing, mm/s)", "5. Maintenance schedule"]:
        assert expected in sections
    assert all(c.page in (1, 2) for c in chunks)


def test_numbered_steps_are_not_treated_as_headings():
    for c in chunk_pdf(MANUALS / "compressor_manual.pdf"):
        assert not c.section.startswith(("1. Clean", "1. Verify", "1. Perform", "1. Measure"))
    tr = next(c for c in chunk_pdf(MANUALS / "compressor_manual.pdf") if c.section.startswith("4.1"))
    assert "1. Clean the cooler fins" in tr.text


def test_tables_keep_rows_together():
    thresholds = next(c for c in chunk_pdf(MANUALS / "compressor_manual.pdf") if c.section.startswith("2."))
    assert "Sensor: pressure | Location: outlet | Unit: bar | Normal: approx. 7.5 | " \
           "HIGH (warning): > 8.5 | HIGH_HIGH (critical): > 9.5" in thresholds.text


def test_machine_type_filter(index):
    hits = index.search("bearing replacement", machine_type="pump", k=5)
    assert hits and {h["machine_type"] for h in hits} == {"Pump"}


def test_unknown_machine_type_raises(index):
    with pytest.raises(ValueError, match="Unknown machine_type"):
        index.search("anything", machine_type="Toaster")


def test_identical_sections_are_folded(index):
    hits = index.search("what to do on a communication fault alarm", k=5)
    texts = [h["text"] for h in hits]
    assert len(texts) == len(set(texts))
    response = next(h for h in hits if h["section"].startswith("3."))
    assert len(response["also_in"]) == 5          # same alarm-response text in all 6 manuals


def test_retrieval_quality(index):
    r = evaluate(index, use_filter=False)
    assert r["recall@3"] >= 0.8, r["misses"]
