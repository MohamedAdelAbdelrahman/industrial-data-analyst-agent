"""Start the MCP server over stdio and call its tools, the way the agent will.

Usage:
    python scripts/try_mcp_tools.py                            # demo questions for every tool
    python scripts/try_mcp_tools.py sql "your database question"
    python scripts/try_mcp_tools.py manuals "your manual question" [machine_type]
    python scripts/try_mcp_tools.py chain                      # SQL -> dataset -> run_python plot
"""
import asyncio
import json
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

DEMO = [
    ("sql", ["Which 3 machines had the most critical alarms, and how many each?"]),
    ("sql", ["What was the total corrective maintenance cost per plant?"]),
    ("manuals", ["compressor discharge temperature too high", "Compressor"]),
    ("manuals", ["how often to change the conveyor gearbox oil"]),
    ("chain", []),
]

CHAIN_QUESTION = "Daily average discharge temperature of Compressor-03 (date, avg_temp), ordered by date"
CHAIN_CODE = """
import pandas as pd, matplotlib.pyplot as plt
df = pd.read_csv("/data/{ds}.csv")
df.columns = ["date", "avg_temp"]
print(df.describe().round(2))
ax = df.plot(x="date", y="avg_temp", figsize=(8, 3), legend=False, title="Compressor-03 discharge temperature")
ax.axhline(105, color="orange", ls="--", label="HIGH 105 degC")
ax.set_ylabel("degC")
plt.tight_layout()
plt.savefig("compressor03_temp.png")
"""


def payload(result) -> dict:
    out = result.structuredContent or json.loads(result.content[0].text)
    return out.get("result", out)


async def call(session, kind, args):
    if kind == "sql":
        out = payload(await session.call_tool("query_database", {"question": args[0], "max_rows": 5}))
        print(f"\n[query_database] {args[0]}\nSQL: {out['sql']}")
        if out["error"]:
            print("ERROR:", out["error"])
            return
        print(out["columns"])
        for row in out["rows"]:
            print(row)
        print(f"({out['row_count']} rows, dataset_id={out['dataset_id']})")
    elif kind == "chain":
        out = payload(await session.call_tool("query_database", {"question": CHAIN_QUESTION, "max_rows": 3}))
        print(f"\n[query_database] {CHAIN_QUESTION}\nSQL: {out['sql']}\n"
              f"-> {out['row_count']} rows, dataset_id={out['dataset_id']}, error={out['error']}")
        if out["error"] or not out["dataset_id"]:
            return
        res = payload(await session.call_tool("run_python", {
            "code": CHAIN_CODE.format(ds=out["dataset_id"]), "dataset_ids": [out["dataset_id"]]}))
        print(f"[run_python] exit={res.get('exit_code')} error={res.get('error')}")
        print(res.get("stdout", "") + res.get("stderr", ""))
        for img in res.get("images", []):
            print("image:", img["path"])
    else:
        params = {"query": args[0], "k": 3} | ({"machine_type": args[1]} if len(args) > 1 else {})
        out = payload(await session.call_tool("search_manuals", params))
        print(f"\n[search_manuals] {params}")
        if out["error"]:
            print("ERROR:", out["error"])
        for h in out["hits"]:
            print(f"- {h['score']:.3f}  {h['doc']} p.{h['page']}  {h['section']}")
            print("    " + h["text"][:160].replace("\n", " / "))


async def main(jobs):
    server = StdioServerParameters(command=sys.executable, args=["-m", "ida.mcp_server.server"])
    async with stdio_client(server) as (read, write), ClientSession(read, write) as session:
        await session.initialize()
        print("tools:", [t.name for t in (await session.list_tools()).tools])
        for kind, args in jobs:
            await call(session, kind, args)


if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] in ("sql", "manuals"):
        jobs = [(sys.argv[1], sys.argv[2:])]
    elif sys.argv[1:] == ["chain"]:
        jobs = [("chain", [])]
    else:
        jobs = DEMO
    asyncio.run(main(jobs))
