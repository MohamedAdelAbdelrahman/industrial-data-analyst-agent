"""Streamlit UI with a fake graph (no API, no MCP server)."""
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from langchain_core.messages import AIMessage

from ida.ui import runtime as rt

APP = str(Path(__file__).resolve().parents[1] / "src" / "ida" / "ui" / "app.py")

PLAN = [{"id": 1, "goal": "Count HIGH alarms per compressor", "tool": "query_database",
         "status": "pending", "result": None}]
EVENT = {"step_id": 1, "name": "query_database", "args": {"question": "alarms per compressor"},
         "output": {"sql": "SELECT machine, COUNT(*) FROM alarms GROUP BY 1", "columns": ["machine", "n"],
                    "rows": [["Compressor-03", 12]], "row_count": 1, "error": None},
         "error": None, "duration_s": 0.4}
USAGE = {"fake-model": {"input": 100, "output": 20, "calls": 2}}
CHUNKS = [
    {"input_guard": {"guard_flags": []}},
    {"planner": {"plan": PLAN, "current": 0, "tool_log": [], "tool_calls": 0, "usage": USAGE}},
    {"executor": {"step_messages": [AIMessage(content="", tool_calls=[
        {"name": "query_database", "args": EVENT["args"], "id": "c1"}])]}},
    {"tools": {"tool_log": [EVENT], "tool_calls": 1}},
    {"step_check": {"plan": [PLAN[0] | {"status": "done", "result": "Compressor-03: 12"}], "current": 1}},
    {"answerer": {"final_answer": "Compressor-03 had 12 alarms ($0 cost).", "status": "answered"}},
    {"output_guard": None},
]


class FakeGraph:
    def __init__(self, fail=False):
        self.fail = fail
        self.questions = []

    async def astream(self, inputs, stream_mode, config):
        self.questions.append(inputs["question"])
        for chunk in CHUNKS:
            if self.fail and "tools" in chunk:
                raise ValueError("tool server died")
            yield chunk


def opener(graph):
    @asynccontextmanager
    async def open_graph():
        yield graph
    return open_graph


@pytest.fixture(autouse=True)
def no_tracing(monkeypatch):
    """.env (loaded by ida.config) may hold Langfuse keys; fake runs must not send traces."""
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
    monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)


def test_runtime_streams_updates_and_keeps_the_session():
    graph = FakeGraph()
    r = rt.AgentRuntime(opener(graph))
    try:
        for _ in range(2):   # the same session serves several questions
            items = list(r.stream("q"))
            assert items[0] == ("trace", None)
            assert [i[1] for i in items[1:]] == [next(iter(c)) for c in CHUNKS]
            assert items[-1] == ("update", "output_guard", {})   # None is normalized to {}
        assert graph.questions == ["q", "q"]
    finally:
        r.close()


def test_runtime_reports_errors():
    r = rt.AgentRuntime(opener(FakeGraph(fail=True)))
    try:
        items = list(r.stream("q"))
        assert items[-1][0] == "error" and "tool server died" in items[-1][1]
    finally:
        r.close()


def test_runtime_start_failure():
    @asynccontextmanager
    async def broken():
        raise OSError("no server")
        yield

    with pytest.raises(RuntimeError, match="no server"):
        rt.AgentRuntime(broken)


def test_app_runs_a_question(monkeypatch):
    from streamlit.testing.v1 import AppTest

    class FakeRuntime(rt.AgentRuntime):
        def __init__(self):
            super().__init__(opener(FakeGraph()))

    monkeypatch.setattr(rt, "AgentRuntime", FakeRuntime)
    at = AppTest.from_file(APP, default_timeout=30)
    at.run()
    at.chat_input[0].set_value("Which compressor had the most alarms?").run()
    assert not at.exception
    answer = at.chat_message[1].markdown[0].value
    assert answer == r"Compressor-03 had 12 alarms (\$0 cost)."   # $ escaped, not LaTeX
    assert any("SELECT machine" in c.value for c in at.code)
    assert {m.label: m.value for m in at.metric}["Tool calls"] == "1"
    assert "Compressor-03: 12" in " ".join(m.value for m in at.markdown)
