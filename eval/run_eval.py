"""Agent eval (Step 10): run the tasks in eval/tasks.yaml end to end and score the answers.

Each finished task is appended to eval/results/<run>/<config>.jsonl straight away, so a run that hits the
free-tier quota can be continued the next day: tasks already there are skipped. Scoring reads the saved
answers again, so a checker can be fixed without asking the models a second time.

Usage:
    python eval/run_eval.py --config both                # each task with both SQL backends, back to back
    python eval/run_eval.py --config llm                 # only SQL written by Gemini
    python eval/run_eval.py --config finetuned           # only SQL written by the fine-tuned Qwen in Ollama
    python eval/run_eval.py --config llm --only sql_active_alarms,ref_off_topic
    python eval/run_eval.py --report                     # score both configs, write report.md + summary.json
    python eval/run_eval.py --arch single --config llm --run single_v2   # one model with all tools, no plan
    python eval/run_eval.py --compare prompts_v2 single_v2               # two runs side by side
"""
import argparse
import asyncio
import json
import os
import re
import sqlite3
import sys
import time
from collections import Counter
from contextlib import AsyncExitStack
from datetime import datetime
from pathlib import Path

import yaml

EVAL_DIR = Path(__file__).resolve().parent
TASKS = EVAL_DIR / "tasks.yaml"
PRICES = EVAL_DIR / "prices.yaml"
RESULTS = EVAL_DIR / "results"
CONFIGS = ("llm", "finetuned")
ARCHS = ("planner", "single")     # ida.agent.graph (planner/executor) or ida.agent.single
CATEGORIES = ("sql_only", "manual_only", "sql+manual", "sql+python", "multi_hop", "should_refuse")
CHECK_KEYS = {"expect_sql", "mention", "numbers", "manual", "chart", "refuse"}
TASK_KEYS = CHECK_KEYS | {"id", "category", "question", "note", "tol"}
DEFAULT_TOL = 0.01
MAX_WAIT_S = 900          # wait out per-minute limits and 503 blocks; a longer block (a daily limit) stops the run
PAUSE_S = 5               # between runs, to stay under the per-minute limits
MAX_TRIES = 3             # per task and config; a failed run is not saved


# ---------- tasks ----------

def load_tasks(path: Path = TASKS) -> list[dict]:
    tasks = yaml.safe_load(path.read_text(encoding="utf-8"))
    seen = set()
    for t in tasks:
        unknown = set(t) - TASK_KEYS
        if unknown or t["category"] not in CATEGORIES or not CHECK_KEYS & set(t) or t["id"] in seen:
            raise ValueError(f"bad task {t.get('id')}: unknown keys {unknown}, category {t.get('category')}, "
                             f"or no check / duplicate id")
        seen.add(t["id"])
    return tasks


# ---------- checks (pure functions of the saved run) ----------

# not part of a name or date: "Pump-03" and "2026-08-12" do not count as the numbers 3, 8 and 12
NUMBER = re.compile(r"(?<![\w.-])\d[\d,]*(?:\.\d+)?")


def numbers_in(text: str) -> list[float]:
    """Every number in the text; thousands separators are dropped ('13,721.97' -> 13721.97)."""
    out = []
    for m in NUMBER.findall(text):
        try:
            out.append(float(m.replace(",", "")))
        except ValueError:
            pass
    return out


def has_number(text: str, target: float, tol: float = DEFAULT_TOL) -> bool:
    """Within tol (relative), and at least 0.005 so a rounded 2.0047 -> '2.00' still counts."""
    return any(abs(n - abs(target)) <= max(tol * abs(target), 0.005) for n in numbers_in(text))


def has_text(text: str, needle: str) -> bool:
    """Case-insensitive substring; 're:<pattern>' is a regular expression instead."""
    if needle.startswith("re:"):
        return re.search(needle[3:], text, re.IGNORECASE) is not None
    return needle.lower() in f" {text} ".lower()


def _any(items) -> list:
    return items if isinstance(items, list) else [items]


def reference_cells(db_path: Path, sql: str) -> list:
    with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as con:
        return [c for row in con.execute(sql).fetchall() for c in row if c is not None]


def cell_in(text: str, cell, tol: float) -> bool:
    return has_number(text, cell, tol) if isinstance(cell, (int, float)) else has_text(text, str(cell))


def check(task: dict, result: dict, db_path: Path) -> dict[str, str | None]:
    """One entry per check the task lists: None = passed, otherwise what is missing."""
    answer, log, tol = result.get("final_answer") or "", result.get("tool_log", []), task.get("tol", DEFAULT_TOL)
    refused = result.get("status") == "refused"
    out: dict[str, str | None] = {}
    if task.get("refuse"):
        out["refuse"] = None if refused else f"not refused (status {result.get('status')})"
        return out
    out["not_refused"] = "refused" if refused else None
    if "expect_sql" in task:
        missing = [c for c in reference_cells(db_path, task["expect_sql"]) if not cell_in(answer, c, tol)]
        out["expect_sql"] = f"missing {missing}" if missing else None
    if "mention" in task:
        missing = [m for m in task["mention"] if not any(has_text(answer, s) for s in _any(m))]
        out["mention"] = f"missing {missing}" if missing else None
    if "numbers" in task:
        missing = [n for n in task["numbers"] if not any(has_number(answer, x, tol) for x in _any(n))]
        out["numbers"] = f"missing {missing}" if missing else None
    if "manual" in task:
        want = task["manual"]
        hits = [h for e in log if e["name"] == "search_manuals" for h in e["output"].get("hits", [])]
        ok = any((not want.get("machine_type") or h.get("machine_type") == want["machine_type"])
                 and h.get("section", "").startswith(want.get("section", "")) for h in hits)
        out["manual"] = None if ok else f"no hit for {want} in {len(hits)} hits"
    if task.get("chart"):
        images = sum(len(e["output"].get("images") or []) for e in log if e["name"] == "run_python")
        out["chart"] = None if images else "no chart"
    return out


def sql_hit(task: dict, result: dict, db_path: Path) -> bool | None:
    """Text-to-SQL inside the agent: did any query_database result contain every reference cell?
    None when the task has no reference SQL."""
    if "expect_sql" not in task:
        return None
    tol = task.get("tol", DEFAULT_TOL)
    cells = reference_cells(db_path, task["expect_sql"])
    for e in result.get("tool_log", []):
        if e["name"] == "query_database" and not e["output"].get("error"):
            text = json.dumps(e["output"].get("rows", []), default=str)
            if all(cell_in(text, c, tol) for c in cells):
                return True
    return False


# ---------- running ----------

def results_path(run: str, config: str) -> Path:
    return RESULTS / run / f"{config}.jsonl"


def saved(run: str, config: str) -> dict[str, dict]:
    p = results_path(run, config)
    if not p.exists():
        return {}
    rows = [json.loads(line) for line in p.read_text(encoding="utf-8").splitlines() if line.strip()]
    return {r["id"]: r for r in rows}      # a re-run of a task replaces the older line


def summarize(final: dict, elapsed: float) -> dict:
    usage = final.get("usage", {})
    return {"status": final.get("status"), "final_answer": final.get("final_answer"),
            "tool_log": final.get("tool_log", []), "plan": final.get("plan", []),
            "guard_flags": final.get("guard_flags", []), "stop_reason": final.get("stop_reason"),
            "tool_calls": final.get("tool_calls", 0), "llm_calls": sum(u["calls"] for u in usage.values()),
            "tokens": sum(u["input"] + u["output"] for u in usage.values()), "usage": usage,
            "latency_s": round(elapsed, 1),
            "sql_backends": sorted({e["output"].get("sql_backend") for e in final.get("tool_log", [])
                                    if e["name"] == "query_database" and e["output"].get("sql_backend")})}


async def run_one(graph, task: dict, config: str, run: str, arch: str = "planner") -> dict:
    from ida.agent.tracing import traced_run
    final: dict = {}
    start = time.monotonic()
    with traced_run(task["question"], tags=["eval", f"config:{config}", f"run:{run}", f"arch:{arch}"],
                    metadata={"task_id": task["id"], "category": task["category"]}) as trace:
        try:
            async for chunk in graph.astream({"question": task["question"]}, stream_mode="updates",
                                             config=trace.config):
                for update in chunk.values():
                    final |= update or {}
        except Exception as e:  # noqa: BLE001 -- the caller decides whether to stop or go on
            final = {"status": "error", "final_answer": f"{type(e).__name__}: {e}"}
    return {"id": task["id"], "category": task["category"], "config": config, "run": run, "arch": arch,
            "question": task["question"], "finished_at": datetime.now().isoformat(timespec="seconds"),
            "trace_url": trace.url, **summarize(final, time.monotonic() - start)}


async def run_tasks(configs: list[str], run: str, tasks: list[dict], redo: bool, arch: str = "planner") -> None:
    """Each task runs under every config back to back, so both see the same free-tier models: the pools
    move to other models as quotas run out, and that must not look like a difference between backends."""
    from ida.agent.graph import build_graph, default_models
    from ida.agent.mcp_client import mcp_tools
    from ida.agent.single import build_single_graph, default_single_models
    from ida.agent.tracing import flush

    done = {c: {} if redo else saved(run, c) for c in configs}
    other = {r.get("arch", "planner") for c in CONFIGS for r in saved(run, c).values()} - {arch}
    if other:
        sys.exit(f"run {run!r} already holds {sorted(other)} results; use another --run name for --arch {arch}")
    todo = [(t, c) for t in tasks for c in configs if t["id"] not in done[c]]
    print(", ".join(f"[{c}] {len(done[c])} saved" for c in configs) + f"; {len(todo)} runs to do", flush=True)
    if not todo:
        return
    (RESULTS / run).mkdir(parents=True, exist_ok=True)
    async with AsyncExitStack() as stack:
        graphs = {}
        for c in configs:
            os.environ["SQL_BACKEND"] = c       # before the MCP server starts: it inherits this environment
            tools = await stack.enter_async_context(mcp_tools())
            graphs[c] = (build_single_graph(default_single_models(tools), tools) if arch == "single"
                         else build_graph(default_models(tools), tools))
        for i, (task, config) in enumerate(todo, 1):
            print(f"\n[{i}/{len(todo)} {config}] {task['id']}: {task['question']}", flush=True)
            for _ in range(MAX_TRIES):
                wait = pool_wait_s(config, arch)
                if wait > MAX_WAIT_S:
                    print(f"stopping: a model pool stays blocked for {wait / 60:.0f} min (daily quota?); "
                          "run the same command again later")
                    flush()
                    return
                if wait:
                    print(f"  every model of a pool is blocked, waiting {wait:.0f} s", flush=True)
                    await asyncio.sleep(wait + 1)
                row = await run_one(graphs[config], task, config, run, arch)
                await asyncio.sleep(PAUSE_S)
                if row["status"] != "error":
                    break
                print(f"  run failed, not saved: {row['final_answer'][:200]}", flush=True)
            else:
                continue          # tried again on the next start
            wrong = [b for b in row["sql_backends"] if not b.startswith(config + ":")]
            if wrong:
                sys.exit(f"query_database used {wrong}, not {config}: check SQL_BACKEND reaches the MCP server")
            with results_path(run, config).open("a", encoding="utf-8") as f:
                f.write(json.dumps(row, default=str) + "\n")
            failed = {k: v for k, v in check(task, row, db_path()).items() if v}
            print(f"  [{row['status']}] {'PASS' if not failed else 'FAIL ' + json.dumps(failed)[:300]}  "
                  f"({row['tool_calls']} tools, {row['llm_calls']} LLM calls, {row['latency_s']} s)", flush=True)
    flush()


def pool_wait_s(config: str, arch: str = "planner") -> float:
    """Seconds until every pool the run needs has a free model (0 = go now)."""
    from ida.config import get_settings
    from ida.model_pool import default_memory
    st, memory, now = get_settings(), default_memory(), datetime.now().astimezone()
    pools = [st.planner_models] + ([st.executor_models] if arch == "planner" else [])
    pools += [st.sql_llm_models] if config == "llm" else []
    waits = []
    for pool in pools:
        untils = [memory.blocked_until(m) for m in pool]
        waits.append(0.0 if None in untils else min((u - now).total_seconds() for u in untils))
    return max(0.0, *waits)


# ---------- report ----------

def db_path() -> Path:
    from ida.config import get_settings
    return get_settings().db_path


def score_run(run: str, tasks: list[dict]) -> dict[str, list[dict]]:
    scored = {}
    for config in CONFIGS:
        rows = saved(run, config)
        scored[config] = []
        for t in tasks:
            if t["id"] in rows:
                r = rows[t["id"]]
                checks = check(t, r, db_path())
                scored[config].append({**r, "checks": checks, "passed": not any(checks.values()),
                                       "sql_hit": sql_hit(t, r, db_path()), **accounting(r)})
    return scored


# ---------- tokens and cost ----------

def load_prices(path: Path = PRICES) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def sql_usage(result: dict) -> dict[str, dict] | None:
    """Tokens of the SQL-writing model inside the MCP server, per model. None when a query_database call
    has no record (runs saved before the tool reported its usage)."""
    calls = [e for e in result.get("tool_log", []) if e["name"] == "query_database"]
    if any("sql_usage" not in e["output"] for e in calls):
        return None
    out: dict[str, dict] = {}
    for e in calls:
        u = e["output"]["sql_usage"]
        if u:          # None: the generator failed before it answered
            prev = out.get(u["model"], {"input": 0, "output": 0, "calls": 0})
            out[u["model"]] = {"input": prev["input"] + u["input"], "output": prev["output"] + u["output"],
                               "calls": prev["calls"] + 1}
    return out


def cost_usd(usage: dict[str, dict], prices: dict) -> tuple[float, set[str]]:
    """Paid-tier cost of the tokens in `usage`, and the models with no price (counted as 0)."""
    total, unknown = 0.0, set()
    for model, u in usage.items():
        if any(model.startswith(p) for p in prices.get("free_prefixes", [])):
            continue
        price = prices["models"].get(model)
        if price is None:
            unknown.add(model)
            continue
        total += (u["input"] * price["input"] + u["output"] * price["output"]) / 1e6
    return total, unknown


def accounting(result: dict, prices: dict | None = None) -> dict:
    """agent_tokens / sql_tokens (None = not recorded) / cost_usd (agent + SQL) / unpriced models.
    cost_complete is False when Gemini wrote SQL whose tokens were not recorded (local SQL is free anyway)."""
    prices = prices or load_prices()
    sql = sql_usage(result)
    agent_cost, unknown = cost_usd(result.get("usage", {}), prices)
    sql_cost, sql_unknown = cost_usd(sql or {}, prices)
    return {"agent_tokens": sum(u["input"] + u["output"] for u in result.get("usage", {}).values()),
            "sql_tokens": None if sql is None else sum(u["input"] + u["output"] for u in sql.values()),
            "cost_usd": agent_cost + sql_cost, "unpriced": sorted(unknown | sql_unknown),
            "cost_complete": sql is not None or all(b.startswith("finetuned:") for b in result.get("sql_backends", []))}


def _avg_sql_tokens(rs: list[dict]) -> str:
    if any(r["sql_tokens"] is None for r in rs):
        return "not recorded"
    return f"{sum(r['sql_tokens'] for r in rs) / len(rs):,.0f}"


def _avg_cost(rs: list[dict]) -> str:
    agent_only = "" if all(r["cost_complete"] for r in rs) else " (agent only)"
    return f"${sum(r['cost_usd'] for r in rs) / len(rs):.4f}{agent_only}"


def _cost_note(prices: dict) -> str:
    return (f"Cost: what the run would cost at Gemini paid-tier prices (`eval/prices.yaml`, checked "
            f"{prices['checked']}); the project itself runs on the free tier for $0. The fine-tuned SQL model "
            "runs locally and costs nothing. \"agent only\" / `*`: the run was saved before `query_database` "
            "reported the SQL model's tokens (`?`), so Gemini's SQL calls are missing from the cost.")


def _pct(n: int, d: int) -> str:
    return f"{n}/{d} ({n / d:.0%})" if d else "-"


def report(run: str, tasks: list[dict]) -> str:
    scored = score_run(run, tasks)
    per_cat = Counter(t["category"] for t in tasks)
    archs = sorted({r.get("arch", "planner") for rs in scored.values() for r in rs}) or ["planner"]
    lines = [f"Run `{run}` ({' + '.join(archs)} agent), {len(tasks)} tasks (`eval/tasks.yaml`).", "",
             "| | " + " | ".join(f"`SQL_BACKEND={c}`" for c in CONFIGS) + " |",
             "|---|" + "---|" * len(CONFIGS)]

    def row(label, fn):
        lines.append(f"| {label} | " + " | ".join(fn(scored[c]) for c in CONFIGS) + " |")

    row("tasks run", lambda rs: f"{len(rs)} of {len(tasks)}")
    row("**passed**", lambda rs: _pct(sum(r["passed"] for r in rs), len(rs)))
    for cat in CATEGORIES:
        row(f"{cat} ({per_cat[cat]})", lambda rs, cat=cat: _pct(
            sum(r["passed"] for r in rs if r["category"] == cat), sum(r["category"] == cat for r in rs)))
    row("reference rows found by a query", lambda rs: _pct(
        sum(r["sql_hit"] is True for r in rs), sum(r["sql_hit"] is not None for r in rs)))
    for label, key in (("avg tool calls", "tool_calls"), ("avg LLM calls", "llm_calls"),
                       ("avg agent tokens", "agent_tokens"), ("avg latency (s)", "latency_s")):
        row(label, lambda rs, key=key: f"{sum(r[key] for r in rs) / len(rs):,.1f}" if rs else "-")
    row("avg SQL-model tokens", lambda rs: _avg_sql_tokens(rs) if rs else "-")
    row("avg cost per task", lambda rs: _avg_cost(rs) if rs else "-")
    row("total cost", lambda rs: f"${sum(r['cost_usd'] for r in rs):.3f}" if rs else "-")
    prices = load_prices()
    unpriced = sorted({m for rs in scored.values() for r in rs for m in r["unpriced"]})
    lines += ["", "LLM calls and agent tokens are the agent's own calls (planner, executor and answerer, or the "
              "single agent). Under `llm` every query_database call is one more Gemini call inside the MCP "
              "server (SQL-model tokens); under `finetuned` it runs locally in Ollama.", "", _cost_note(prices)]
    if unpriced:
        lines += ["", f"No price in `eval/prices.yaml` (counted as $0): {', '.join(unpriced)}."]
    lines += ["", "Per task: failed checks (`-` = passed, blank = not run), tokens (agent + SQL model) and cost:", "",
              "| task | " + " | ".join(f"{c} | tokens | cost" for c in CONFIGS) + " |",
              "|---|" + "---|---:|---:|" * len(CONFIGS)]
    by_id = {c: {r["id"]: r for r in scored[c]} for c in CONFIGS}
    for t in tasks:
        cells = []
        for c in CONFIGS:
            r = by_id[c].get(t["id"])
            if r is None:
                cells += ["", "", ""]
                continue
            sql = "?" if r["sql_tokens"] is None else f"{r['sql_tokens']:,}"
            cost = f"${r['cost_usd']:.4f}" + ("" if r["cost_complete"] else "*")
            cells += [_failed_checks(r), f"{r['agent_tokens']:,} + {sql}", cost]
        lines.append(f"| {t['id']} | " + " | ".join(cells) + " |")
    md = "\n".join(lines) + "\n"
    out = RESULTS / run
    out.mkdir(parents=True, exist_ok=True)
    (out / "report.md").write_text(md, encoding="utf-8")
    summary = {c: {"passed": sum(r["passed"] for r in scored[c]), "run": len(scored[c]),
                   "failed": {r["id"]: {k: v for k, v in r["checks"].items() if v}
                              for r in scored[c] if not r["passed"]},
                   "models": dict(sum((Counter({m: u["calls"]}) for r in scored[c]
                                       for m, u in r["usage"].items()), Counter())),
                   "cost_usd": round(sum(r["cost_usd"] for r in scored[c]), 4),
                   "sql_tokens_recorded": all(r["sql_tokens"] is not None for r in scored[c])}
               for c in CONFIGS}
    (out / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return md


def _failed_checks(r: dict) -> str:
    return "-" if r["passed"] else ", ".join(k for k, v in r["checks"].items() if v)


def _compare_config(config: str, runs: dict[str, list[dict]]) -> list[str]:
    (name_a, ra), (name_b, rb) = runs.items()
    arch = {n: "+".join(sorted({r.get("arch", "planner") for r in rs})) for n, rs in runs.items()}
    lines = ["", f"### `SQL_BACKEND={config}`", "",
             f"| | `{name_a}` ({arch[name_a]}) | `{name_b}` ({arch[name_b]}) |", "|---|---|---|"]

    def row(label, fn):
        lines.append(f"| {label} | {fn(ra)} | {fn(rb)} |")

    row("**passed**", lambda rs: _pct(sum(r["passed"] for r in rs), len(rs)))
    for cat in CATEGORIES:
        row(cat, lambda rs, cat=cat: _pct(sum(r["passed"] for r in rs if r["category"] == cat),
                                          sum(r["category"] == cat for r in rs)))
    row("partial (a budget ended the run)", lambda rs: str(sum(r["status"] == "partial" for r in rs)))
    for label, key in (("avg tool calls", "tool_calls"), ("avg LLM calls", "llm_calls"),
                       ("avg agent tokens", "agent_tokens"), ("avg latency (s)", "latency_s")):
        row(label, lambda rs, key=key: f"{sum(r[key] for r in rs) / len(rs):,.1f}")
    row("avg SQL-model tokens", _avg_sql_tokens)
    row("avg cost per task", _avg_cost)
    row("LLM calls per model", lambda rs: ", ".join(
        f"{m} {n}" for m, n in sum((Counter({m: u["calls"]}) for r in rs for m, u in r["usage"].items()),
                                   Counter()).most_common()))
    by_a, by_b = {r["id"]: r for r in ra}, {r["id"]: r for r in rb}
    diff = [i for i in by_a if i in by_b and by_a[i]["passed"] != by_b[i]["passed"]]
    lines += ["", "Tasks with different results (failed checks, `-` = passed):", ""]
    lines += [f"- `{i}`: {name_a} {_failed_checks(by_a[i])}; {name_b} {_failed_checks(by_b[i])}"
              for i in diff] or ["- none"]
    return lines


def compare(run_a: str, run_b: str, tasks: list[dict]) -> str:
    """Two runs side by side (e.g. planner/executor vs single agent), per SQL backend both have results."""
    a, b = score_run(run_a, tasks), score_run(run_b, tasks)
    lines = [f"`{run_a}` vs `{run_b}`, {len(tasks)} tasks (`eval/tasks.yaml`)."]
    for config in CONFIGS:
        if a[config] and b[config]:
            lines += _compare_config(config, {run_a: a[config], run_b: b[config]})
    lines += ["", _cost_note(load_prices())]
    md = "\n".join(lines) + "\n"
    (RESULTS / f"compare_{run_a}_vs_{run_b}.md").write_text(md, encoding="utf-8")
    return md


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--config", choices=(*CONFIGS, "both"), help="run the tasks with this SQL backend")
    ap.add_argument("--run", default="baseline", help="results folder name (eval/results/<run>/)")
    ap.add_argument("--only", help="comma-separated task ids")
    ap.add_argument("--redo", action="store_true", help="run the selected tasks again even if saved")
    ap.add_argument("--report", action="store_true", help="score the saved results and write the report")
    ap.add_argument("--arch", choices=ARCHS, default="planner", help="agent graph to run (default planner)")
    ap.add_argument("--compare", nargs=2, metavar=("RUN_A", "RUN_B"), help="write a side-by-side table of two runs")
    a = ap.parse_args()
    all_tasks = load_tasks()
    selected = [t for t in all_tasks if not a.only or t["id"] in a.only.split(",")]
    if a.only and len(selected) != len(a.only.split(",")):
        sys.exit(f"unknown task id in {a.only}")
    if a.compare:
        print(compare(*a.compare, all_tasks))
        sys.exit()
    if a.config:
        asyncio.run(run_tasks(list(CONFIGS) if a.config == "both" else [a.config], a.run, selected, a.redo,
                              a.arch))
    if a.report or not a.config:
        print(report(a.run, all_tasks))
