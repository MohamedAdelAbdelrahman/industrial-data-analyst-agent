import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def pytest_addoption(parser):
    parser.addoption("--live", action="store_true", help="run tests that call the real LLM API")


def pytest_collection_modifyitems(config, items):
    if config.getoption("--live"):
        return
    skip = pytest.mark.skip(reason="needs --live (calls the LLM API)")
    for item in items:
        if "live" in item.keywords:
            item.add_marker(skip)


@pytest.fixture(scope="session")
def small_db(tmp_path_factory) -> Path:
    """A 10-day database, built once per test session."""
    out = tmp_path_factory.mktemp("db") / "sensors.db"
    subprocess.run([sys.executable, str(ROOT / "data" / "generate_db.py"), "--out", str(out), "--days", "10"],
                   check=True, capture_output=True)
    return out
