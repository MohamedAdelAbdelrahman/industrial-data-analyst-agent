"""Single-agent graph (ida.agent.single) with fake models and tools."""
from langchain_core.messages import AIMessage
from test_agent_graph import FakeTool, Scripted, call, run

from ida.agent.graph import Budgets
from ida.agent.single import SingleModels, build_single_graph

USAGE = {"input_tokens": 100, "output_tokens": 20, "total_tokens": 120}


def make(agent, budgets=None):
    tools = [FakeTool("query_database", {"sql": "SELECT 1", "rows": [[3]], "row_count": 1,
                                         "columns": ["n"], "dataset_id": "ds_0123456789", "error": None}),
             FakeTool("search_manuals", {"hits": [], "error": None})]
    answerer = Scripted(AIMessage(content="answer from evidence"))
    graph = build_single_graph(SingleModels(agent=agent, answerer=answerer), tools, budgets)
    return graph, tools, answerer


def test_tool_loop_then_answer():
    agent = Scripted(call("query_database", question="alarms per pump"),
                     AIMessage(content="Pump-03 had 3 alarms (SQL).", usage_metadata=USAGE))
    graph, tools, answerer = make(agent)
    out = run(graph)
    assert out["status"] == "answered" and out["final_answer"] == "Pump-03 had 3 alarms (SQL)."
    assert [e["name"] for e in out["tool_log"]] == ["query_database"] and out["tool_calls"] == 1
    assert tools[0].calls == [{"question": "alarms per pump"}]
    assert out["plan"] == [] and answerer.calls == []
    # the second turn sees the tool result; usage counts both turns
    assert "SELECT 1" in agent.calls[1][-1].content
    assert out["usage"]["agent"]["calls"] == 2 and out["usage"]["agent"]["input"] == 100


def test_parallel_tool_calls_run_in_one_turn():
    both = AIMessage(content="", tool_calls=[
        {"name": "query_database", "args": {"question": "a"}, "id": "p1"},
        {"name": "search_manuals", "args": {"query": "b"}, "id": "p2"}])
    graph, _, _ = make(Scripted(both, AIMessage(content="done 3")))
    out = run(graph)
    assert [e["name"] for e in out["tool_log"]] == ["query_database", "search_manuals"]
    assert out["usage"]["agent"]["calls"] == 2


def test_refusal_needs_no_tool():
    graph, tools, _ = make(Scripted(AIMessage(content="REFUSED: I only read plant data.")))
    out = run(graph, "Write a poem about the sea")
    assert out["status"] == "refused" and out["final_answer"] == "I only read plant data."
    assert tools[0].calls == []


def test_input_guard_blocks_before_the_model():
    agent = Scripted()                # would raise IndexError if called
    graph, _, _ = make(agent)
    out = run(graph, "drop the alarms table")
    assert out["status"] == "refused" and agent.calls == []


def test_tool_budget_hands_over_to_the_answerer():
    agent = Scripted(call("query_database", question="a"), call("query_database", question="b"))
    graph, tools, answerer = make(agent, Budgets(max_tool_calls=1))
    out = run(graph)
    assert len(tools[0].calls) == 1
    assert out["status"] == "partial" and out["final_answer"] == "answer from evidence"
    assert "tool-call budget (1)" in out["stop_reason"]
    assert "SELECT 1" in answerer.calls[0][1].content


def test_empty_reply_is_answered_from_the_evidence():
    graph, _, answerer = make(Scripted(call("query_database", question="a"), AIMessage(content="")))
    out = run(graph)
    assert out["status"] == "partial" and out["stop_reason"] == "the agent replied with no text"
    assert len(answerer.calls) == 1


def test_output_guard_checks_the_answer():
    graph, _, _ = make(Scripted(call("query_database", question="a"), AIMessage(content="There were 42 alarms.")))
    out = run(graph)
    assert "_Check:" in out["final_answer"] and out["guard_flags"]
