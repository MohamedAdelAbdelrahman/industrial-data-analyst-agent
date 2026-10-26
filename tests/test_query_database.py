"""query_database: validator, read-only execution, timeout, dataset cache and the tool wrapper."""
import dataclasses
import sqlite3

import pytest

from ida.config import get_settings
from ida.mcp_server.datasets import DatasetStore
from ida.mcp_server.db import QueryTimeoutError, run_query
from ida.mcp_server.sql_generator import extract_sql
from ida.mcp_server.sql_validator import SQLValidationError, validate_sql
from ida.mcp_server.tools import QueryDatabaseTool

# ---------- validator ----------

@pytest.mark.parametrize("sql", [
    "SELECT COUNT(*) FROM machines",
    "select name from machines where status = 'running';",
    "WITH c AS (SELECT machine_id, COUNT(*) n FROM alarms GROUP BY 1) SELECT m.name, c.n FROM c JOIN machines m USING (machine_id)",
    "SELECT sensor_id FROM readings WHERE value IS NULL UNION SELECT sensor_id FROM alarms",
    "SELECT * FROM alarms WHERE alarm_id IN (SELECT related_alarm_id FROM maintenance_events)",
])
def test_validator_accepts_read_queries(sql):
    assert validate_sql(sql) == sql.strip().rstrip(";")


@pytest.mark.parametrize("sql, reason", [
    ("DELETE FROM alarms", "Only SELECT"),
    ("DROP TABLE alarms", "Only SELECT"),
    ("UPDATE machines SET status = 'idle'", "Only SELECT"),
    ("INSERT INTO machines (name) VALUES ('x')", "Only SELECT"),
    ("PRAGMA table_info(machines)", "Only SELECT"),
    ("ATTACH DATABASE 'other.db' AS o", "Only SELECT"),
    ("VACUUM", "Only SELECT"),
    ("SELECT 1; DROP TABLE alarms", "Exactly one"),
    ("SELECT load_extension('evil')", "Forbidden function"),
    ("SELECT * FROM sqlite_master", "Unknown table"),
    ("SELECT * FROM users", "Unknown table"),
    ("", "Empty"),
])
def test_validator_rejects(sql, reason):
    with pytest.raises(SQLValidationError, match=reason):
        validate_sql(sql)


# ---------- execution ----------

def test_connection_is_read_only_even_without_validator(small_db):
    with pytest.raises(sqlite3.OperationalError, match="readonly|read-only|query_only"):
        run_query(small_db, "DELETE FROM alarms", max_rows=10, timeout_s=5)
    assert run_query(small_db, "SELECT COUNT(*) FROM alarms", 10, 5).rows[0][0] > 0


def test_row_cap_sets_truncated(small_db):
    res = run_query(small_db, "SELECT * FROM readings", max_rows=100, timeout_s=10)
    assert len(res.rows) == 100 and res.truncated
    assert res.columns == ["reading_id", "sensor_id", "ts", "value", "quality"]


def test_timeout_aborts_long_query(small_db):
    with pytest.raises(QueryTimeoutError):
        run_query(small_db, "SELECT COUNT(*) FROM readings a, readings b", max_rows=1, timeout_s=0.5)


# ---------- dataset store ----------

def test_dataset_store_roundtrip(tmp_path):
    store = DatasetStore(tmp_path)
    ds = store.save("SELECT 1", ["a", "b"], [(1, None), (2, "x")])
    assert store.exists(ds)
    assert store.path(ds).read_text(encoding="utf-8").splitlines() == ["a,b", "1,", "2,x"]
    with pytest.raises(ValueError):
        store.path("../../etc/passwd")


# ---------- generator output parsing ----------

@pytest.mark.parametrize("raw", [
    "SELECT 1",
    "```sql\nSELECT 1\n```",
    "Here is the query:\n```\nSELECT 1\n```\nThis counts...",
    "Sure! SELECT 1",
])
def test_extract_sql(raw):
    assert extract_sql(raw) == "SELECT 1"


# ---------- tool wrapper (fake generator, no API calls) ----------

class FakeGenerator:
    name = "fake"

    def __init__(self, sql):
        self.sql = sql

    def generate(self, question):
        return self.sql


def make_tool(small_db, tmp_path, sql):
    settings = dataclasses.replace(get_settings(), db_path=small_db, dataset_dir=tmp_path)
    return QueryDatabaseTool(settings, FakeGenerator(sql), DatasetStore(tmp_path))


def test_tool_success(small_db, tmp_path):
    out = make_tool(small_db, tmp_path, "SELECT machine_type, COUNT(*) n FROM machines GROUP BY 1 ORDER BY 1")(
        "machines per type", max_rows=3)
    assert out["error"] is None
    assert out["columns"] == ["machine_type", "n"]
    assert len(out["rows"]) == 3 and out["row_count"] == 6 and out["truncated"]
    assert (tmp_path / f"{out['dataset_id']}.csv").exists()


def test_tool_reports_rejected_sql(small_db, tmp_path):
    out = make_tool(small_db, tmp_path, "DROP TABLE alarms")("delete everything")
    assert out["error"].startswith("Rejected SQL") and out["rows"] == []


def test_tool_reports_sql_error(small_db, tmp_path):
    out = make_tool(small_db, tmp_path, "SELECT no_such_column FROM machines")("bad column")
    assert out["error"].startswith("SQL error")


def test_tool_empty_result_has_no_dataset(small_db, tmp_path):
    out = make_tool(small_db, tmp_path, "SELECT * FROM machines WHERE 1 = 0")("nothing")
    assert out["error"] is None and out["row_count"] == 0 and out["dataset_id"] is None


# ---------- live (pytest --live) ----------

@pytest.mark.live
def test_live_llm_generator(small_db, tmp_path):
    from ida.mcp_server.sql_generator import make_generator
    settings = dataclasses.replace(get_settings(), db_path=small_db, dataset_dir=tmp_path)
    tool = QueryDatabaseTool(settings, make_generator(settings), DatasetStore(tmp_path))
    out = tool("How many machines are there of each machine type?")
    assert out["error"] is None, out
    assert sum(r[-1] for r in out["rows"]) == 27
