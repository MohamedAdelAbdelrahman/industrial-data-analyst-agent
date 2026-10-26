"""Read-only SQLite execution with a wall-clock timeout and a row cap."""
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path


class QueryTimeoutError(RuntimeError):
    pass


@dataclass
class QueryResult:
    columns: list[str]
    rows: list[tuple]
    truncated: bool          # more rows existed than max_rows


def connect_readonly(db_path: Path) -> sqlite3.Connection:
    if not db_path.exists():
        raise FileNotFoundError(f"Database not found: {db_path} (run data/generate_db.py)")
    con = sqlite3.connect(f"{db_path.resolve().as_uri()}?mode=ro", uri=True, check_same_thread=False)
    con.execute("PRAGMA query_only = ON")
    return con


def run_query(db_path: Path, sql: str, max_rows: int, timeout_s: float) -> QueryResult:
    con = connect_readonly(db_path)
    deadline = time.monotonic() + timeout_s
    # called every 10k VM instructions; a non-zero return aborts the query
    con.set_progress_handler(lambda: int(time.monotonic() > deadline), 10_000)
    try:
        cur = con.execute(sql)
        columns = [d[0] for d in cur.description or []]
        rows = cur.fetchmany(max_rows + 1)
    except sqlite3.OperationalError as e:
        if "interrupted" in str(e):
            raise QueryTimeoutError(f"Query exceeded {timeout_s:g}s timeout") from e
        raise
    finally:
        con.close()
    return QueryResult(columns, rows[:max_rows], truncated=len(rows) > max_rows)
