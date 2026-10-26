"""Start the MCP server over stdio, list its tools, and ask query_database a few questions.

Usage:
    python scripts/try_query_database.py ["your own question" ...]
"""
import asyncio
import json
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

DEFAULT_QUESTIONS = [
    "Which 3 machines had the most critical alarms, and how many each?",
    "How many alarms are still active right now, per machine type?",
    "What was the total corrective maintenance cost per plant?",
]


async def main(questions):
    server = StdioServerParameters(command=sys.executable, args=["-m", "ida.mcp_server.server"])
    async with stdio_client(server) as (read, write), ClientSession(read, write) as session:
        await session.initialize()
        tools = await session.list_tools()
        resources = await session.list_resources()
        print("tools:    ", [t.name for t in tools.tools])
        print("resources:", [str(r.uri) for r in resources.resources])
        for q in questions:
            result = await session.call_tool("query_database", {"question": q, "max_rows": 5})
            out = result.structuredContent or json.loads(result.content[0].text)
            out = out.get("result", out)
            print(f"\nQ: {q}\nSQL: {out['sql']}")
            if out["error"]:
                print("ERROR:", out["error"])
            else:
                print(out["columns"])
                for row in out["rows"]:
                    print(row)
                print(f"({out['row_count']} rows, dataset_id={out['dataset_id']})")


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1:] or DEFAULT_QUESTIONS))
