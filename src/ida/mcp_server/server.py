"""FastMCP server "industrial-tools".

Run:
    python -m ida.mcp_server.server                      # stdio (used by the agent)
    python -m ida.mcp_server.server --transport http     # streamable HTTP on :8765
    mcp dev src/ida/mcp_server/server.py                 # MCP Inspector
"""
import argparse
from functools import lru_cache

from mcp.server.fastmcp import FastMCP

from ida.config import get_settings
from ida.mcp_server.datasets import DatasetStore
from ida.mcp_server.sql_generator import load_schema, make_generator
from ida.mcp_server.tools import QueryDatabaseTool

mcp = FastMCP("industrial-tools", port=8765)


@lru_cache
def _query_tool() -> QueryDatabaseTool:
    s = get_settings()
    return QueryDatabaseTool(s, make_generator(s), DatasetStore(s.dataset_dir))


@mcp.resource("schema://sensors", mime_type="text/plain")
def schema() -> str:
    """SQLite schema of the industrial sensor database (machines, sensors, readings, alarms, maintenance)."""
    return load_schema(str(get_settings().schema_path))


@mcp.tool()
def query_database(question: str, max_rows: int = 50) -> dict:
    """Answer a question about the plant's machines, sensors, readings, alarms or maintenance
    by generating and running a read-only SQL query.

    Pass a precise natural-language question (not SQL), e.g.
    "Number of critical alarms per machine in September 2026".
    Returns the generated SQL, columns, up to max_rows rows (max 200), the total row_count and a
    dataset_id that run_python can load as a CSV. If 'error' is set, rephrase and retry.
    """
    return _query_tool()(question, max_rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--transport", choices=["stdio", "http"], default="stdio")
    args = ap.parse_args()
    mcp.run(transport="streamable-http" if args.transport == "http" else "stdio")


if __name__ == "__main__":
    main()
