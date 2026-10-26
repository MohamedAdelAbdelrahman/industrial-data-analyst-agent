"""Tool implementations, kept free of MCP plumbing so they are easy to unit-test."""
import sqlite3

from ida.config import Settings
from ida.mcp_server.datasets import DatasetStore
from ida.mcp_server.db import QueryTimeoutError, run_query
from ida.mcp_server.sql_generator import SQLGenerator
from ida.mcp_server.sql_validator import SQLValidationError, validate_sql


class QueryDatabaseTool:
    def __init__(self, settings: Settings, generator: SQLGenerator, store: DatasetStore):
        self.settings = settings
        self.generator = generator
        self.store = store

    def __call__(self, question: str, max_rows: int = 50) -> dict:
        max_rows = max(1, min(max_rows, 200))
        out = {"question": question, "sql": None, "columns": [], "rows": [], "row_count": 0,
               "truncated": False, "dataset_id": None, "sql_backend": self.generator.name, "error": None}
        try:
            out["sql"] = self.generator.generate(question)
        except Exception as e:  # noqa: BLE001 -- tool boundary: API down, rate limit, Ollama not running
            out["error"] = f"SQL generator unavailable ({type(e).__name__}): {str(e)[:300]}"
            return out
        try:
            sql = validate_sql(out["sql"])
            res = run_query(self.settings.db_path, sql, self.settings.max_cached_rows,
                            self.settings.query_timeout_s)
        except SQLValidationError as e:
            out["error"] = f"Rejected SQL: {e}"
            return out
        except QueryTimeoutError as e:
            out["error"] = f"{e}. Ask a narrower question (filter by machine or time range)."
            return out
        except sqlite3.Error as e:
            out["error"] = f"SQL error: {e}. Try rephrasing the question."
            return out

        out.update(columns=res.columns, rows=[list(r) for r in res.rows[:max_rows]],
                   row_count=len(res.rows), truncated=res.truncated or len(res.rows) > max_rows)
        if res.rows:
            out["dataset_id"] = self.store.save(sql, res.columns, res.rows)
        if res.truncated:
            out["note"] = f"Result capped at {self.settings.max_cached_rows} rows; aggregate in SQL instead."
        return out


class RunPythonTool:
    MAX_CODE_CHARS = 20_000

    def __init__(self, sandbox, store: DatasetStore):
        self.sandbox = sandbox
        self.store = store

    def __call__(self, code: str, dataset_ids: list[str] | None = None, timeout_s: int = 20) -> dict:
        dataset_ids = dataset_ids or []
        if len(code) > self.MAX_CODE_CHARS:
            return {"error": f"Code too long ({len(code)} chars, max {self.MAX_CODE_CHARS})."}
        try:
            missing = [d for d in dataset_ids if not self.store.exists(d)]
        except ValueError as e:
            return {"error": str(e)}
        if missing:
            return {"error": f"Unknown dataset_id(s): {missing}. Use dataset_id values returned by query_database."}

        result = self.sandbox.run(code, {d: self.store.path(d) for d in dataset_ids},
                                  timeout_s=max(1, min(timeout_s, 60)))
        if result["timed_out"]:
            result["error"] = f"Timed out after {timeout_s}s and was killed."
        elif result["oom_killed"]:
            result["error"] = "Killed: exceeded the 512 MB memory limit."
        elif result["exit_code"] != 0:
            result["error"] = f"Exited with code {result['exit_code']}; see stderr."
        else:
            result["error"] = None
        return result
