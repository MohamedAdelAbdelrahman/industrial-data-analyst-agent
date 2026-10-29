"""Streamlit UI: chat on the left, the agent's live steps on the right.

    streamlit run src/ida/ui/app.py

Each run is kept in st.session_state as the list of graph updates it streamed, so the step panel of
any earlier question can be redrawn with the same code that drew it live.
"""
import time
from pathlib import Path

import pandas as pd
import streamlit as st

from ida.agent.run import DEMO
from ida.ui.runtime import AgentRuntime

st.set_page_config(page_title="Industrial Data Analyst", page_icon="🏭", layout="wide")

STATUS_ICON = {"answered": "✅", "partial": "⚠️", "refused": "🛑", "error": "❌"}
TOOL_ICON = {"query_database": "🗄️", "search_manuals": "📖", "run_python": "🐍"}


@st.cache_resource(show_spinner="Starting the MCP tool server (about 20 s)...")
def runtime() -> AgentRuntime:
    return AgentRuntime()


def md(text: str) -> str:
    """Streamlit renders $...$ as LaTeX; answers about costs contain dollar signs."""
    return str(text).replace("$", r"\$")


def images(final: dict) -> list[str]:
    return [i["path"] for e in final.get("tool_log", []) if e["name"] == "run_python"
            for i in e["output"].get("images", []) if Path(i["path"]).exists()]


# ---------- step panel ----------

class StepPanel:
    """Draws graph updates into a container. Tracks the merged state, like ida.agent.run.ask, because
    the tools node streams the whole tool_log and only the new events should be drawn."""

    def __init__(self, box, metrics_box, start: float):
        self.box, self.metrics_box, self.start = box, metrics_box, start
        self.final: dict = {}

    def add(self, node: str, update: dict) -> None:
        seen_events = len(self.final.get("tool_log", []))
        seen_flags = len(self.final.get("guard_flags", []))
        with self.box:
            self._draw(node, update, seen_events, seen_flags)
        self.final |= update

    def _draw(self, node: str, u: dict, seen_events: int, seen_flags: int) -> None:
        if node == "input_guard" and u.get("status") == "refused":
            st.error(f"**Input guard blocked the question:** {'; '.join(u['guard_flags'])}")
        elif node == "planner":
            if u.get("status") == "refused":
                st.warning(f"**Planner refused:** {md(u['final_answer'])}")
            if u.get("plan"):
                st.markdown("**Plan**\n" + "\n".join(f"{s['id']}. {TOOL_ICON.get(s['tool'], '')} "
                                                     f"`{s['tool']}`: {md(s['goal'])}" for s in u["plan"]))
        elif node == "tools":
            for e in u["tool_log"][seen_events:]:
                tool_event(e)
            for f in u.get("guard_flags", [])[seen_flags:]:
                st.warning(f"Guard: {f}")
        elif node == "step_check":
            s = u["plan"][u["current"] - 1]
            icon = "✔️" if s["status"] == "done" else "✖️"
            st.markdown(f"{icon} **Step {s['id']} {s['status']}**: {md(str(s['result'])[:400])}")
        elif node == "replanner":
            st.markdown("**🔁 Replanned**\n" + "\n".join(f"{s['id']}. `{s['tool']}`: {md(s['goal'])}"
                                                       for s in u["plan"][u["current"]:]))
        elif node == "output_guard" and u.get("guard_flags"):
            st.warning("**Output guard:** " + "; ".join(u["guard_flags"][seen_flags:]))

    def metrics(self, elapsed: float | None = None) -> None:
        usage = self.final.get("usage", {}).values()
        plan = self.final.get("plan", [])
        cols = self.metrics_box.container().columns(5)
        cols[0].metric("Steps", f"{sum(s['status'] != 'pending' for s in plan)}/{len(plan)}")
        cols[1].metric("Tool calls", self.final.get("tool_calls", 0))
        cols[2].metric("LLM calls", sum(u["calls"] for u in usage))
        cols[3].metric("Tokens", f"{sum(u['input'] + u['output'] for u in usage):,}")
        cols[4].metric("Time", f"{elapsed if elapsed is not None else time.monotonic() - self.start:.0f} s")


def tool_event(e: dict) -> None:
    o, args = e["output"], e["args"]
    label = f"{TOOL_ICON.get(e['name'], '')} {e['name']} ({e['duration_s']} s)"
    with st.expander(label + (" — error" if o.get("error") else ""), expanded=e["name"] != "search_manuals"):
        if e["name"] == "query_database":
            st.caption(md(args.get("question", "")))
            if o.get("sql"):
                st.code(o["sql"], language="sql")
            if o.get("columns"):
                st.dataframe(pd.DataFrame(o["rows"], columns=o["columns"]), hide_index=True,
                             height=min(35 * (len(o["rows"]) + 1) + 3, 250))
                st.caption(f"{o['row_count']} rows" + (" (truncated)" if o.get("truncated") else ""))
        elif e["name"] == "search_manuals":
            st.caption(md(args.get("query", "")) + (f" · {args['machine_type']}" if args.get("machine_type") else ""))
            for h in o.get("hits", [])[:5]:
                st.markdown(f"**{h['doc']} p.{h['page']}** · {md(h['section'])}  \n{md(h['text'][:300])}...")
        elif e["name"] == "run_python":
            st.code(args.get("code", ""), language="python")
            if o.get("stdout"):
                st.text(o["stdout"][:2000])
            if o.get("stderr") and o.get("error"):
                st.text(o["stderr"][-1500:])
            for img in o.get("images", []):
                if Path(img["path"]).exists():
                    st.image(img["path"], caption=img["name"])
        if o.get("error"):
            st.error(o["error"])


def answer_message(run: dict) -> None:
    final = run["final"]
    with st.chat_message("assistant"):
        status = final.get("status", "error")
        st.markdown(md(final.get("final_answer", "(no answer)")))
        for path in images(final):
            st.image(path)
        st.caption(f"{STATUS_ICON.get(status, '')} {status}" +
                   (f" · stopped: {final['stop_reason']}" if final.get("stop_reason") else ""))


def run_question(question: str, steps_col) -> dict:
    start = time.monotonic()
    with steps_col:
        st.subheader("Agent steps")
        metrics_box = st.empty()
        trace_box = st.empty()
        panel = StepPanel(st.container(), metrics_box, start)
    run = {"question": question, "updates": [], "trace_url": None}
    with st.spinner("Working..."):
        for item in runtime().stream(question):
            if item[0] == "trace":
                run["trace_url"] = item[1]
                if item[1]:
                    trace_box.markdown(f"[Langfuse trace ↗]({item[1]})")
            elif item[0] == "update":
                run["updates"].append(item[1:])
                panel.add(*item[1:])
                panel.metrics()
            else:
                panel.final |= {"status": "error", "final_answer": item[1]}
    run["final"] = panel.final
    run["elapsed"] = time.monotonic() - start
    return run


def replay(run: dict) -> None:
    st.subheader("Agent steps")
    metrics_box = st.empty()
    if run["trace_url"]:
        st.markdown(f"[Langfuse trace ↗]({run['trace_url']})")
    panel = StepPanel(st.container(), metrics_box, 0)
    for node, update in run["updates"]:
        panel.add(node, update)
    panel.final = run["final"]
    panel.metrics(run["elapsed"])


# ---------- page ----------

runs: list[dict] = st.session_state.setdefault("runs", [])

with st.sidebar:
    st.header("🏭 Industrial Data Analyst")
    st.caption("LangGraph planner/executor over an MCP server: SQL on sensor data, manual search, "
               "sandboxed Python.")
    st.subheader("Demo questions")
    for i, q in enumerate(DEMO):
        if st.button(q, key=f"demo{i}", use_container_width=True):
            st.session_state.pending = q
    st.divider()
    if st.button("Clear chat"):
        runs.clear()
        st.rerun()
    if st.button("Restart tool server", help="Use if the MCP server crashed or after changing .env"):
        runtime().close()
        runtime.clear()
        st.rerun()

chat_col, steps_col = st.columns([3, 2], gap="large")

with chat_col:
    for run in runs:
        st.chat_message("user").markdown(md(run["question"]))
        answer_message(run)
    question = st.chat_input("Ask about machines, alarms, sensor readings, maintenance or the manuals")
    question = question or st.session_state.pop("pending", None)

if question:
    with chat_col:
        st.chat_message("user").markdown(md(question))
    runs.append(run_question(question, steps_col))
    st.rerun()
elif runs:
    with steps_col:
        selected = st.selectbox("Show steps for", range(len(runs)), index=len(runs) - 1,
                                format_func=lambda i: f"{i + 1}. {runs[i]['question'][:70]}")
        replay(runs[selected])
else:
    steps_col.info("Ask a question or pick a demo question. The plan, every tool call (SQL, manual hits, "
                   "plots) and the cost counters appear here while the agent works.")
