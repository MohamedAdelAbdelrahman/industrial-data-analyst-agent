"""Langfuse tracing: one trace per question, with every graph node, LLM call and MCP tool call
as nested observations. Disabled automatically when LANGFUSE_* keys are not set."""
import os
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any


def tracing_enabled() -> bool:
    return bool(os.environ.get("LANGFUSE_PUBLIC_KEY") and os.environ.get("LANGFUSE_SECRET_KEY"))


@dataclass
class RunTrace:
    config: dict[str, Any]                 # pass to graph.astream / ainvoke
    trace_id: str | None = None
    url: str | None = None
    tags: list[str] = field(default_factory=list)


@contextmanager
def traced_run(question: str, tags: list[str] | None = None, session_id: str | None = None,
               metadata: dict[str, Any] | None = None, recursion_limit: int = 100) -> Iterator[RunTrace]:
    tags = ["agent", *(tags or [])]
    base = {"recursion_limit": recursion_limit}
    if not tracing_enabled():
        yield RunTrace(config=base, tags=tags)
        return

    from langfuse import Langfuse, get_client, propagate_attributes
    from langfuse.langchain import CallbackHandler

    client = get_client()
    trace_id = Langfuse.create_trace_id()
    handler = CallbackHandler(trace_context={"trace_id": trace_id})
    with propagate_attributes(trace_name="agent-run", tags=tags, session_id=session_id,
                              metadata={k: str(v) for k, v in (metadata or {}).items()}):
        yield RunTrace(config=base | {"callbacks": [handler], "run_name": "agent-run"},
                       trace_id=trace_id, url=client.get_trace_url(trace_id=trace_id), tags=tags)


def flush() -> None:
    if tracing_enabled():
        from langfuse import get_client
        get_client().flush()
