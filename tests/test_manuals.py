"""The manuals agree with the DB generator and their text is extractable for retrieval."""
import re
import subprocess
import sys
from pathlib import Path

import pytest

from ida.manuals import extract_pages

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "data"))
from build_manuals import TROUBLESHOOTING
from generate_db import PROFILES


@pytest.fixture(scope="module")
def manuals(tmp_path_factory):
    out = tmp_path_factory.mktemp("manuals")
    subprocess.run([sys.executable, str(ROOT / "data" / "build_manuals.py"), "--out", str(out)],
                   check=True, capture_output=True)
    return {m: "\n".join(extract_pages(out / f"{m.lower().replace(' ', '_')}_manual.pdf")) for m in PROFILES}


@pytest.mark.parametrize("mtype", list(PROFILES))
def test_thresholds_match_db_profiles(manuals, mtype):
    text = manuals[mtype]
    for stype, unit, loc, _base, _noise, warn, crit in PROFILES[mtype]["sensors"]:
        assert f"> {warn:g}" in text and f"> {crit:g}" in text, (mtype, stype)


@pytest.mark.parametrize("mtype", list(PROFILES))
def test_troubleshooting_section_per_sensor(manuals, mtype):
    for stype, unit, loc, *_ in PROFILES[mtype]["sensors"]:
        assert stype in TROUBLESHOOTING[mtype]
        assert f"High {stype} ({loc}, {unit})" in manuals[mtype]


@pytest.mark.parametrize("mtype", list(PROFILES))
def test_part_numbers_survive_extraction(manuals, mtype):
    expected = {p for causes, actions in TROUBLESHOOTING[mtype].values()
                for p in re.findall(r"SP-[A-Z]+-[A-Z0-9]+-[A-Z0-9]+", " ".join(actions))}
    assert expected
    for part in expected:
        assert part in manuals[mtype], part


def test_no_ligatures_or_boilerplate(manuals):
    for text in manuals.values():
        assert "ﬁ" not in text and "Page 1 of" not in text
