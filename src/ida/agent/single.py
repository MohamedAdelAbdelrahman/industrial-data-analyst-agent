"""Single-agent graph: one model with all tools in a ReAct loop, no plan.

    input_guard ─┬─ blocked ─────────────────────────────────────────────► END
                 └─► agent ⇄ tools
                       │ reply without tool calls
                       ├─ "REFUSED: ..." ──────────────────────────────────► END
                       ├─ answer ─────────────────────────► output_guard ─► END
                       └─ budget hit / empty reply ─► wrap_up ─► output_guard

It is the baseline for the planner/executor graph (ida.agent.graph): same tools, guards, budgets and
prompt rules, but the model decides everything in one conversation. `python eval/run_eval.py --arch
single` runs it on the eval tasks. The state has the same keys as the planner graph (an empty plan),
so the eval, the CLI printer and the usage accounting work for both.
"""
from dataclasses import dataclass, field

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.runnables import Runnable
from langchain_core.tools import BaseTool
from langgraph.graph import END, START, StateGraph

from ida.agent import prompts
from ida.agent.graph import Budgets, _add_usage, _evidence, _llm_calls, call_tools, check_answer, guard
from ida.agent.state import AgentState

STEP_ID = 1      # tool events belong to one "step": the whole conversation


@dataclass
class SingleModels:
    agent: Runnable            # chat model with tools bound
    answerer: Runnable         # plain chat model, only used when a budget ends the loop
    names: dict[str, str] = field(default_factory=dict)


def _text(ai: AIMessage) -> str:
    return (ai.text if hasattr(ai, "text") else str(ai.content)).strip()


def build_single_graph(models: SingleModels, tools: list[BaseTool], budgets: Budgets | None = None):
    budgets = budgets or Budgets()
    tools_by_name = {t.name: t for t in tools}
    system = prompts.SINGLE.format(max_tool_calls=budgets.max_tool_calls)

    async def agent(state: AgentState) -> dict:
        start = {} if state.get("step_messages") else {
            "plan": [], "current": 0, "tool_log": [], "tool_calls": 0, "step_tool_calls": 0,
            "replans": 0, "stop_reason": None}
        msgs = state.get("step_messages") or [SystemMessage(system), HumanMessage(state["question"])]
        ai = await models.agent.ainvoke(msgs)
        return start | {"step_messages": [*msgs, ai],
                        "usage": _add_usage(state.get("usage", {}), models.names.get("agent", "agent"), ai)}

    def after_agent(state: AgentState) -> str:
        ai = state["step_messages"][-1]
        if not getattr(ai, "tool_calls", None):
            return "finish" if _text(ai) else "wrap_up"
        if (_llm_calls(state) >= budgets.max_llm_calls
                or state.get("tool_calls", 0) + len(ai.tool_calls) > budgets.max_tool_calls):
            return "wrap_up"
        return "tools"

    async def run_tools(state: AgentState) -> dict:
        return await call_tools(tools_by_name, state, STEP_ID)

    def finish(state: AgentState) -> dict:
        text = _text(state["step_messages"][-1])
        if text.upper().startswith(prompts.REFUSED) and not state.get("tool_log"):
            reason = text[len(prompts.REFUSED):].strip() or "I can't help with that request."
            return {"status": "refused", "final_answer": reason}
        return {"status": "answered", "final_answer": text}

    async def wrap_up(state: AgentState) -> dict:
        """The loop ended without an answer: the answerer writes one from the tool evidence."""
        ai = state["step_messages"][-1]
        if not getattr(ai, "tool_calls", None):
            reason = "the agent replied with no text"
        elif _llm_calls(state) >= budgets.max_llm_calls:
            reason = f"LLM-call budget ({budgets.max_llm_calls}) exhausted"
        else:
            reason = f"tool-call budget ({budgets.max_tool_calls}) exhausted"
        out = await models.answerer.ainvoke([
            SystemMessage(prompts.ANSWERER),
            HumanMessage(f"Question: {state['question']}\n\n{_evidence(state)}\n\n"
                         f"NOTE: the run stopped early ({reason}).")])
        return {"final_answer": _text(out), "status": "partial", "stop_reason": reason,
                "usage": _add_usage(state.get("usage", {}), models.names.get("answerer", "answerer"), out)}

    g = StateGraph(AgentState)
    g.add_node("input_guard", guard)
    g.add_node("agent", agent)
    g.add_node("tools", run_tools)
    g.add_node("finish", finish)
    g.add_node("wrap_up", wrap_up)
    g.add_node("output_guard", check_answer)
    g.add_edge(START, "input_guard")
    g.add_conditional_edges("input_guard", lambda s: END if s.get("status") == "refused" else "agent",
                            ["agent", END])
    g.add_conditional_edges("agent", after_agent, ["tools", "finish", "wrap_up"])
    g.add_edge("tools", "agent")
    g.add_conditional_edges("finish", lambda s: END if s["status"] == "refused" else "output_guard",
                            ["output_guard", END])
    g.add_edge("wrap_up", "output_guard")
    g.add_edge("output_guard", END)
    return g.compile()


def default_single_models(tools: list[BaseTool]) -> SingleModels:
    """The agent uses the planner pool (the strongest models): it plans, runs the tools and answers."""
    from ida.config import get_settings
    from ida.model_pool import model_pool
    s = get_settings()
    return SingleModels(agent=model_pool("agent", s.planner_models, lambda m: m.bind_tools(tools)),
                        answerer=model_pool("answerer", s.planner_models),
                        names={"agent": s.planner_models[0], "answerer": s.planner_models[0]})
