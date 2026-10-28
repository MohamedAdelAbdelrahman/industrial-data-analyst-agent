"""Planner / executor graph.

    input_guard ─┬─ blocked ─────────────────────────────────────────────────► END
                 └─► planner ─┬─ refused ────────────────────────────────────► END
                              └─► executor ⇄ tools
                                     │ step finished
                                     ▼
                                  step_check ─┬─ next step ─► executor
                                              ├─ failed ────► replanner ─► executor | answerer
                                              └─ all done / budget hit ─► answerer ─► output_guard ─► END

Guards (ida.agent.guards): input_guard blocks before any LLM call, tool output reaches the LLMs wrapped
as untrusted data, and output_guard flags answer numbers that no tool result contains.

Models are injected (AgentModels) so the routing logic is unit-testable with fakes.
"""
import json
import time
from dataclasses import dataclass, field
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.runnables import Runnable
from langchain_core.tools import BaseTool
from langgraph.graph import END, START, StateGraph

from ida.agent import prompts
from ida.agent.guards import injection_flags, input_guard, output_guard, wrap_untrusted
from ida.agent.state import AgentState, Plan, StepRecord, ToolEvent
from ida.config import DATA_END

MAX_TOOL_CONTENT = 6_000      # chars of a tool result passed back to the executor


@dataclass
class Budgets:
    max_tool_calls: int = 15
    max_step_tool_calls: int = 4
    max_replans: int = 2
    max_llm_calls: int = 25    # free tier: about 20 requests/day per model, so a runaway loop is costly


@dataclass
class AgentModels:
    planner: Runnable          # -> {"raw": AIMessage, "parsed": Plan}  (with_structured_output(include_raw=True))
    replanner: Runnable        # same as planner
    executor: Runnable         # chat model with tools bound
    answerer: Runnable         # plain chat model
    names: dict[str, str] = field(default_factory=dict)   # role -> model name, for usage accounting


# ---------- helpers ----------

def _add_usage(usage: dict, model: str, msg: Any) -> dict:
    meta = getattr(msg, "usage_metadata", None) or {}
    model = (getattr(msg, "response_metadata", None) or {}).get("model_name") or model   # fallbacks
    u = dict(usage or {})
    prev = u.get(model, {"input": 0, "output": 0, "calls": 0})
    u[model] = {"input": prev["input"] + meta.get("input_tokens", 0),
                "output": prev["output"] + meta.get("output_tokens", 0),
                "calls": prev["calls"] + 1}
    return u


def tool_output(msg: ToolMessage) -> dict:
    """Structured result of an MCP tool call (falls back to parsing the text content)."""
    art = msg.artifact
    sc = art.get("structured_content") if isinstance(art, dict) else getattr(art, "structured_content", None)
    if sc:
        return sc.get("result", sc) if isinstance(sc, dict) else {"result": sc}
    text = msg.text if hasattr(msg, "text") else str(msg.content)
    try:
        out = json.loads(text)
        return out if isinstance(out, dict) else {"result": out}
    except (json.JSONDecodeError, TypeError):
        return {"text": text}


def _datasets(tool_log: list[ToolEvent]) -> str:
    rows = [f"- {e['output']['dataset_id']}: columns {e['output'].get('columns')} "
            f"({e['output'].get('row_count')} rows) for '{e['args'].get('question')}'"
            for e in tool_log if e["name"] == "query_database" and e["output"].get("dataset_id")]
    return "\n".join(rows) or "(none yet)"


def _llm_calls(state: AgentState) -> int:
    return sum(u["calls"] for u in state.get("usage", {}).values())


def _done_steps(plan: list[StepRecord]) -> str:
    return "\n".join(f"{s['id']}. [{s['status']}] {s['goal']} -> {s['result']}"
                     for s in plan if s["status"] != "pending") or "(none yet)"


def _pending_steps(plan: list[StepRecord], current: int) -> str:
    return "\n".join(f"{s['id']}. ({s['tool']}) {s['goal']}" for s in plan[current + 1:]) or "(none)"


def _plan_records(steps, start_id: int) -> list[StepRecord]:
    return [StepRecord(id=start_id + i, goal=s.goal, tool=s.tool, status="pending", result=None)
            for i, s in enumerate(steps)]


def _evidence(state: AgentState) -> str:
    parts = []
    for e in state.get("tool_log", []):
        o = e["output"]
        if e["name"] == "query_database":
            parts.append(f"- query_database '{e['args'].get('question')}'\n  SQL: {o.get('sql')}\n"
                         f"  columns: {o.get('columns')}  rows (first {len(o.get('rows', []))} of "
                         f"{o.get('row_count')}): {json.dumps(o.get('rows', [])[:20])}\n  error: {o.get('error')}")
        elif e["name"] == "search_manuals":
            hits = "\n".join(f"  * {h['doc']} p.{h['page']} [{h['section']}]: {h['text'][:600]}"
                             for h in o.get("hits", [])[:3])
            parts.append(f"- search_manuals '{e['args'].get('query')}'\n{hits}")
        elif e["name"] == "run_python":
            imgs = [i["name"] for i in o.get("images", [])]
            parts.append(f"- run_python (exit {o.get('exit_code')}, error {o.get('error')})\n"
                         f"  stdout: {str(o.get('stdout', ''))[:1500]}\n  charts: {imgs}")
    return ("## Plan and step results\n" + _done_steps(state["plan"]) + "\n\n## Tool evidence\n"
            + wrap_untrusted("\n".join(parts) or "(none)", "tool results"))


# ---------- graph ----------

def build_graph(models: AgentModels, tools: list[BaseTool], budgets: Budgets | None = None):
    budgets = budgets or Budgets()
    tools_by_name = {t.name: t for t in tools}

    def guard(state: AgentState) -> dict:
        res = input_guard(state["question"])
        if res.blocked:
            return {"status": "refused", "final_answer": res.reason, "guard_flags": res.flags}
        return {"guard_flags": []}

    async def planner(state: AgentState) -> dict:
        res = await models.planner.ainvoke([SystemMessage(prompts.PLANNER), HumanMessage(state["question"])])
        plan: Plan = res["parsed"] or Plan(steps=[])
        usage = _add_usage(state.get("usage", {}), models.names.get("planner", "planner"), res["raw"])
        base = {"usage": usage, "current": 0, "step_messages": [], "tool_log": [], "tool_calls": 0,
                "step_tool_calls": 0, "replans": 0, "stop_reason": None}
        if plan.refuse:
            return base | {"plan": [], "status": "refused",
                           "final_answer": plan.refusal_reason or "I can't help with that request."}
        return base | {"plan": _plan_records(plan.steps[:5], 1)}

    async def executor(state: AgentState) -> dict:
        step = state["plan"][state["current"]]
        msgs = state.get("step_messages") or [
            SystemMessage(prompts.EXECUTOR),
            HumanMessage(f"User question: {state['question']}\n\n"
                         f"Earlier steps:\n{_done_steps(state['plan'])}\n\n"
                         f"Available datasets:\n{_datasets(state.get('tool_log', []))}\n\n"
                         f"Current step {step['id']}: {step['goal']}\nSuggested tool: {step['tool']}\n\n"
                         f"Remaining steps (done later, not now):\n{_pending_steps(state['plan'], state['current'])}"),
        ]
        ai = await models.executor.ainvoke(msgs)
        return {"step_messages": [*msgs, ai],
                "usage": _add_usage(state.get("usage", {}), models.names.get("executor", "executor"), ai)}

    async def run_tools(state: AgentState) -> dict:
        ai: AIMessage = state["step_messages"][-1]
        step_id = state["plan"][state["current"]]["id"]
        new_msgs, events, flags = [], [], []
        for call in ai.tool_calls:
            start = time.monotonic()
            tool = tools_by_name.get(call["name"])
            if tool is None:
                output = {"error": f"Unknown tool {call['name']!r}"}
                content = json.dumps(output)
            else:
                msg = await tool.ainvoke({"type": "tool_call", "id": call["id"],
                                          "name": call["name"], "args": call["args"]})
                output = tool_output(msg)
                content = json.dumps(output, default=str)
            if len(content) > MAX_TOOL_CONTENT:
                content = content[:MAX_TOOL_CONTENT] + "... [truncated]"
            flags += [f"step {step_id} {call['name']}: {f}" for f in injection_flags(content)]
            new_msgs.append(ToolMessage(content=wrap_untrusted(content, call["name"]), tool_call_id=call["id"], name=call["name"]))
            events.append(ToolEvent(step_id=step_id, name=call["name"], args=call["args"], output=output,
                                    error=output.get("error"), duration_s=round(time.monotonic() - start, 2)))
        n = len(ai.tool_calls)
        return {"step_messages": [*state["step_messages"], *new_msgs],
                "tool_log": [*state.get("tool_log", []), *events],
                "tool_calls": state.get("tool_calls", 0) + n,
                "step_tool_calls": state.get("step_tool_calls", 0) + n,
                "guard_flags": [*state.get("guard_flags", []), *flags]}

    def after_executor(state: AgentState) -> str:
        ai = state["step_messages"][-1]
        if not getattr(ai, "tool_calls", None):
            return "step_check"
        if _llm_calls(state) >= budgets.max_llm_calls:
            return "step_check"
        if state.get("tool_calls", 0) + len(ai.tool_calls) > budgets.max_tool_calls:
            return "step_check"      # step_check records the budget stop
        if state.get("step_tool_calls", 0) + len(ai.tool_calls) > budgets.max_step_tool_calls:
            return "step_check"
        return "tools"

    async def step_check(state: AgentState) -> dict:
        ai = state["step_messages"][-1]
        plan = [dict(s) for s in state["plan"]]
        step = plan[state["current"]]
        stop_reason = state.get("stop_reason")
        if _llm_calls(state) >= budgets.max_llm_calls:
            stop_reason = stop_reason or f"LLM-call budget ({budgets.max_llm_calls}) exhausted"
        if getattr(ai, "tool_calls", None):          # executor wanted more tools but a budget said no
            if state.get("tool_calls", 0) + len(ai.tool_calls) > budgets.max_tool_calls:
                stop_reason = f"tool-call budget ({budgets.max_tool_calls}) exhausted"
            step.update(status="failed", result="FAILED: " + (
                stop_reason or f"step tool budget ({budgets.max_step_tool_calls}) exhausted"))
        else:
            text = (ai.text if hasattr(ai, "text") else str(ai.content)).strip()
            failed = text.upper().startswith("FAILED")
            step.update(status="failed" if failed else "done", result=text[:1500])
        return {"plan": plan, "current": state["current"] + 1, "step_messages": [], "step_tool_calls": 0,
                "stop_reason": stop_reason}

    def after_step_check(state: AgentState) -> str:
        if state.get("stop_reason"):
            return "answerer"
        last = state["plan"][state["current"] - 1]
        if last["status"] == "failed" and state.get("replans", 0) < budgets.max_replans:
            return "replanner"
        if state["current"] >= len(state["plan"]):
            return "answerer"
        return "executor"

    async def replanner(state: AgentState) -> dict:
        done = [s for s in state["plan"][: state["current"]]]
        res = await models.replanner.ainvoke([
            SystemMessage(prompts.REPLANNER),
            HumanMessage(f"User question: {state['question']}\n\nSteps so far:\n{_done_steps(done)}\n\n"
                         f"Available datasets:\n{_datasets(state.get('tool_log', []))}")])
        plan: Plan = res["parsed"] or Plan(steps=[])
        new = _plan_records(plan.steps[:4], len(done) + 1)
        return {"plan": [*done, *new], "current": len(done), "replans": state.get("replans", 0) + 1,
                "usage": _add_usage(state.get("usage", {}), models.names.get("planner", "planner"), res["raw"])}

    def after_replanner(state: AgentState) -> str:
        return "executor" if state["current"] < len(state["plan"]) else "answerer"

    async def answerer(state: AgentState) -> dict:
        note = f"\n\nNOTE: the run stopped early ({state['stop_reason']})." if state.get("stop_reason") else ""
        ai = await models.answerer.ainvoke([
            SystemMessage(prompts.ANSWERER),
            HumanMessage(f"Question: {state['question']}\n\n{_evidence(state)}{note}")])
        # a failed step counts against the answer unless a replan was made to recover from it
        unrecovered = sum(s["status"] == "failed" for s in state["plan"]) > state.get("replans", 0)
        return {"final_answer": ai.text if hasattr(ai, "text") else str(ai.content),
                "status": "partial" if state.get("stop_reason") or unrecovered else "answered",
                "usage": _add_usage(state.get("usage", {}), models.names.get("answerer", "answerer"), ai)}

    def check_answer(state: AgentState) -> dict:
        # numbers may come from tool results, the question, or the data end date used as "now"
        evidence = " ".join([state["question"], DATA_END,
                             *(json.dumps(e["output"], default=str) for e in state.get("tool_log", []))])
        res = output_guard(state["final_answer"], evidence)
        if not res.flags:
            return {}
        return {"final_answer": state["final_answer"] + "\n\n_Check: " + "; ".join(res.flags) + "._",
                "guard_flags": [*state.get("guard_flags", []), *res.flags]}

    g = StateGraph(AgentState)
    g.add_node("input_guard", guard)
    g.add_node("planner", planner)
    g.add_node("executor", executor)
    g.add_node("tools", run_tools)
    g.add_node("step_check", step_check)
    g.add_node("replanner", replanner)
    g.add_node("answerer", answerer)
    g.add_node("output_guard", check_answer)
    g.add_edge(START, "input_guard")
    g.add_conditional_edges("input_guard", lambda s: END if s.get("status") == "refused" else "planner",
                            ["planner", END])
    g.add_conditional_edges("planner", lambda s: END if s.get("status") == "refused"
                            else ("executor" if s["plan"] else "answerer"), ["executor", "answerer", END])
    g.add_conditional_edges("executor", after_executor, ["tools", "step_check"])
    g.add_edge("tools", "executor")
    g.add_conditional_edges("step_check", after_step_check, ["executor", "replanner", "answerer"])
    g.add_conditional_edges("replanner", after_replanner, ["executor", "answerer"])
    g.add_edge("answerer", "output_guard")
    g.add_edge("output_guard", END)
    return g.compile()


def default_models(tools: list[BaseTool]) -> AgentModels:
    from ida.config import get_settings
    from ida.model_pool import model_pool
    s = get_settings()
    # Each role is a pool: free-tier models hit 429 (quota) / 503 (high demand), so a failing model is
    # skipped and remembered until its quota resets (see ida.model_pool).
    planner = model_pool("planner", s.planner_models, lambda m: m.with_structured_output(Plan, include_raw=True))
    return AgentModels(
        planner=planner, replanner=planner,
        executor=model_pool("executor", s.executor_models, lambda m: m.bind_tools(tools)),
        answerer=model_pool("answerer", s.planner_models),
        names={"planner": s.planner_models[0], "executor": s.executor_models[0],
               "answerer": s.planner_models[0]})
