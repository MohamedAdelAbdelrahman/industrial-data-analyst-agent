"""Run the agent from the command line, printing each step as it happens.

Usage:
    python -m ida.agent.run "Which compressor had the most alarms?"
    python -m ida.agent.run --demo           # the 5 sample questions
"""
import argparse
import asyncio
import json
import time

from ida.agent.graph import build_graph, default_models
from ida.agent.mcp_client import mcp_tools
from ida.agent.tracing import flush, traced_run

DEMO = [
    "Which compressor had the most process alarms (HIGH or HIGH_HIGH), and how many?",
    "What does the manual recommend when a pump makes a gravel-like noise?",
    "Plot the daily maximum discharge temperature of Compressor-03 and compare it with its warning threshold.",
    "Which machine had the most critical vibration alarms, and what does its manual recommend for high vibration?",
    "Delete all alarms that were raised before September.",
]


def summarize_tool(event: dict) -> str:
    o = event["output"]
    if o.get("error"):
        return f"ERROR {o['error']}"
    if event["name"] == "query_database":
        return f"{o.get('row_count')} rows, sql: {o.get('sql')}"
    if event["name"] == "search_manuals":
        return "; ".join(f"{h['doc']} p.{h['page']} {h['section']}" for h in o.get("hits", [])[:3])
    return f"exit {o.get('exit_code')}, images {[i['name'] for i in o.get('images', [])]}, stdout: {o.get('stdout', '')}"


def print_update(node: str, update: dict, seen_events: int = 0, seen_flags: int = 0) -> None:
    if node == "input_guard" and update.get("status") == "refused":
        print(f"[input_guard] BLOCKED ({'; '.join(update['guard_flags'])}): {update['final_answer']}")
    elif node == "output_guard" and update:
        print(f"[output_guard] {'; '.join(update['guard_flags'])}")
    elif node == "planner":
        if update.get("status") == "refused":
            print(f"[planner] REFUSED: {update['final_answer']}")
        for s in update.get("plan", []):
            print(f"[planner] {s['id']}. ({s['tool']}) {s['goal']}")
    elif node == "executor":
        ai = update["step_messages"][-1]
        for c in ai.tool_calls:
            print(f"  [executor] -> {c['name']}({json.dumps(c['args'])[:300]})")
    elif node == "tools":
        for e in update["tool_log"][seen_events:]:
            print(f"  [tools] {e['name']} ({e['duration_s']}s): {summarize_tool(e)[:400]}")
        for f in update.get("guard_flags", [])[seen_flags:]:
            print(f"  [guard] {f}")
    elif node == "step_check":
        s = update["plan"][update["current"] - 1]
        print(f"[step {s['id']} {s['status']}] {str(s['result'])[:300]}")
    elif node == "replanner":
        print("[replanner] new plan:")
        for s in update["plan"][update["current"]:]:
            print(f"   {s['id']}. ({s['tool']}) {s['goal']}")


async def ask(graph, question: str) -> dict:
    print(f"\n{'=' * 100}\nQ: {question}\n{'=' * 100}")
    start = time.monotonic()
    final: dict = {"question": question}
    with traced_run(question, tags=["cli"]) as trace:
        try:
            async for chunk in graph.astream({"question": question}, stream_mode="updates",
                                             config=trace.config):
                for node, update in chunk.items():
                    update = update or {}          # a node that changes nothing streams None
                    print_update(node, update, len(final.get("tool_log", [])), len(final.get("guard_flags", [])))
                    final |= update
        except Exception as e:  # noqa: BLE001 -- report and continue with the next question
            final |= {"status": "error", "final_answer": f"Run failed: {type(e).__name__}: {str(e)[:300]}"}
    final["trace_url"] = trace.url
    tokens = sum(u["input"] + u["output"] for u in final.get("usage", {}).values())
    llm_calls = sum(u["calls"] for u in final.get("usage", {}).values())
    print(f"\n[{final.get('status')}] {final.get('final_answer')}")
    print(f"\n-- {final.get('tool_calls', 0)} tool calls, {llm_calls} LLM calls, {tokens:,} tokens, "
          f"{time.monotonic() - start:.1f}s")
    if trace.url:
        print(f"-- trace: {trace.url}")
    return final


async def main(questions: list[str]) -> None:
    async with mcp_tools() as tools:
        graph = build_graph(default_models(tools), tools)
        for q in questions:
            await ask(graph, q)
    flush()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("question", nargs="*")
    ap.add_argument("--demo", action="store_true")
    a = ap.parse_args()
    asyncio.run(main(DEMO if a.demo or not a.question else [" ".join(a.question)]))
