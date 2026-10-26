"""Agent state and the structured plan the planner/replanner return."""
from typing import Any, Literal, TypedDict

from langchain_core.messages import AnyMessage
from pydantic import BaseModel, Field

ToolName = Literal["query_database", "search_manuals", "run_python"]


class PlanStep(BaseModel):
    goal: str = Field(description="One concrete sub-goal, e.g. 'Count critical alarms per compressor'")
    tool: ToolName = Field(description="The tool this step needs")


class Plan(BaseModel):
    refuse: bool = Field(False, description="True if the request is out of scope or asks to change data")
    refusal_reason: str = Field("", description="Short explanation shown to the user when refuse is true")
    steps: list[PlanStep] = Field(default_factory=list, description="1-5 steps in execution order")


class StepRecord(TypedDict):
    id: int
    goal: str
    tool: str
    status: Literal["pending", "done", "failed"]
    result: str | None


class ToolEvent(TypedDict):
    step_id: int
    name: str
    args: dict[str, Any]
    output: dict[str, Any]
    error: str | None
    duration_s: float


class AgentState(TypedDict, total=False):
    question: str
    plan: list[StepRecord]
    current: int                      # index into plan
    step_messages: list[AnyMessage]   # executor scratchpad for the current step only
    tool_log: list[ToolEvent]
    tool_calls: int
    step_tool_calls: int
    replans: int
    usage: dict[str, dict[str, int]]  # model -> {"input": n, "output": n}
    stop_reason: str | None           # set when a budget ends the run early
    status: Literal["answered", "partial", "refused", "error"]
    final_answer: str
