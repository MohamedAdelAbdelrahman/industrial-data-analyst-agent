"""Agent graph routing with fake models and tools (no API, no MCP server)."""
import asyncio
import json

from langchain_core.messages import AIMessage, ToolMessage

from ida.agent.graph import AgentModels, Budgets, build_graph, tool_output
from ida.agent.state import Plan, PlanStep


class Scripted:
    """Fake runnable returning queued responses; records the messages it was called with."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    async def ainvoke(self, messages):
        self.calls.append(messages)
        return self.responses.pop(0)


def plan(*steps, refuse=False, reason=""):
    return {"raw": AIMessage(content="", usage_metadata={"input_tokens": 10, "output_tokens": 5,
                                                         "total_tokens": 15}),
            "parsed": Plan(refuse=refuse, refusal_reason=reason, steps=[PlanStep(goal=g, tool=t) for g, t in steps])}


def call(name, **args):
    call.n = getattr(call, "n", 0) + 1
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": f"c{call.n}"}])


class FakeTool:
    def __init__(self, name, output):
        self.name = name
        self.output = output
        self.calls = []

    async def ainvoke(self, tool_call):
        self.calls.append(tool_call["args"])
        return ToolMessage(content=json.dumps(self.output), tool_call_id=tool_call["id"], name=self.name,
                           artifact={"structured_content": self.output})


def make(planner, executor, replanner=None, budgets=None):
    tools = [FakeTool("query_database", {"sql": "SELECT 1", "rows": [[1]], "row_count": 1,
                                         "columns": ["x"], "dataset_id": "ds_0123456789", "error": None}),
             FakeTool("search_manuals", {"hits": [{"doc": "pump_manual.pdf", "page": 2, "section": "4.3",
                                                   "text": "cavitation"}], "error": None})]
    answerer = Scripted(AIMessage(content="final answer"))
    models = AgentModels(planner=planner, replanner=replanner or Scripted(), executor=executor, answerer=answerer)
    return build_graph(models, tools, budgets), tools, answerer


def run(graph, question="q"):
    return asyncio.run(graph.ainvoke({"question": question}, config={"recursion_limit": 100}))


def test_two_step_plan_runs_tools_and_answers():
    executor = Scripted(call("query_database", question="count alarms"), AIMessage(content="Pump-03 has 3"),
                        call("search_manuals", query="vibration"), AIMessage(content="manual p.2 says ..."))
    graph, _, answerer = make(Scripted(plan(("count", "query_database"), ("manual", "search_manuals"))), executor)
    out = run(graph)
    assert out["status"] == "answered" and out["final_answer"] == "final answer"
    assert [s["status"] for s in out["plan"]] == ["done", "done"]
    assert [e["name"] for e in out["tool_log"]] == ["query_database", "search_manuals"]
    assert out["tool_calls"] == 2
    # step 2 sees step 1's result and the dataset id; the answerer sees the tool evidence
    step2_prompt = executor.calls[2][1].content
    assert "Pump-03 has 3" in step2_prompt and "ds_0123456789" in step2_prompt
    assert "SELECT 1" in answerer.calls[0][1].content and "pump_manual.pdf p.2" in answerer.calls[0][1].content


def test_executor_is_told_about_remaining_steps():
    executor = Scripted(AIMessage(content="ok"), AIMessage(content="ok"))
    graph, _, _ = make(Scripted(plan(("a", "query_database"), ("plot it", "run_python"))), executor)
    run(graph)
    first = executor.calls[0][1].content
    assert "Remaining steps (done later, not now):\n2. (run_python) plot it" in first


def test_refusal_skips_execution():
    executor = Scripted()
    graph, tools, answerer = make(Scripted(plan(refuse=True, reason="Data changes are not allowed.")), executor)
    out = run(graph, "delete all alarms")
    assert out["status"] == "refused" and out["final_answer"] == "Data changes are not allowed."
    assert executor.calls == [] and answerer.calls == [] and all(t.calls == [] for t in tools)


def test_failed_step_triggers_replan_and_recovers():
    executor = Scripted(AIMessage(content="FAILED: no such column"), AIMessage(content="found it"))
    replanner = Scripted(plan(("rephrased", "query_database")))
    graph, _, _ = make(Scripted(plan(("first try", "query_database"))), executor, replanner)
    out = run(graph)
    assert len(replanner.calls) == 1 and out["replans"] == 1
    assert [s["status"] for s in out["plan"]] == ["failed", "done"]
    assert out["status"] == "answered"


def test_replan_budget_limits_retries():
    executor = Scripted(*[AIMessage(content="FAILED: nope")] * 3)
    replanner = Scripted(plan(("again", "query_database")), plan(("again", "query_database")))
    graph, _, _ = make(Scripted(plan(("try", "query_database"))), executor, replanner, Budgets(max_replans=2))
    out = run(graph)
    assert len(replanner.calls) == 2 and out["status"] == "partial"


def test_global_tool_budget_stops_the_run():
    executor = Scripted(*[call("query_database", question=f"q{i}") for i in range(10)])
    graph, tools, answerer = make(Scripted(plan(("loop", "query_database"))), executor,
                                  budgets=Budgets(max_tool_calls=2, max_step_tool_calls=10))
    out = run(graph)
    assert out["tool_calls"] == 2 and len(tools[0].calls) == 2
    assert "budget" in out["stop_reason"] and out["status"] == "partial"
    assert "stopped early" in answerer.calls[0][1].content


def test_step_tool_budget_fails_the_step():
    executor = Scripted(*[call("query_database", question="q")] * 3)
    replanner = Scripted(plan())
    graph, _, _ = make(Scripted(plan(("greedy", "query_database"))), executor, replanner,
                       Budgets(max_step_tool_calls=2))
    out = run(graph)
    assert out["plan"][0]["status"] == "failed" and "step tool budget" in out["plan"][0]["result"]
    assert len(replanner.calls) == 1


def test_usage_is_accumulated_per_model():
    executor = Scripted(AIMessage(content="ok", usage_metadata={"input_tokens": 100, "output_tokens": 20,
                                                                 "total_tokens": 120}))
    graph, _, _ = make(Scripted(plan(("a", "query_database"))), executor)
    out = run(graph)
    assert out["usage"]["planner"] == {"input": 10, "output": 5, "calls": 1}
    assert out["usage"]["executor"] == {"input": 100, "output": 20, "calls": 1}


def test_tool_output_parsing():
    structured = ToolMessage(content="ignored", tool_call_id="1", artifact={"structured_content": {"result": {"a": 1}}})
    text_only = ToolMessage(content='{"b": 2}', tool_call_id="2")
    plain = ToolMessage(content="not json", tool_call_id="3")
    assert tool_output(structured) == {"a": 1}
    assert tool_output(text_only) == {"b": 2}
    assert tool_output(plain) == {"text": "not json"}
