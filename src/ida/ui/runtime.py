"""Run the agent for Streamlit.

Streamlit reruns the whole script on every click and runs it in worker threads, but the MCP session is
async and the server takes ~20 s to start. So one AgentRuntime owns a background event loop that keeps
the MCP session and the compiled graph alive; `stream()` runs a question on that loop and hands each
graph update back to the Streamlit thread through a queue.

The session is opened and closed inside one long-lived task (`_serve`) because the MCP client uses
anyio task groups, which must be exited by the task that entered them.
"""
import asyncio
import contextlib
import queue
import threading
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import Any

from ida.agent.tracing import flush, traced_run

_DONE = object()


@asynccontextmanager
async def open_agent_graph() -> AsyncIterator[Any]:
    from ida.agent.graph import build_graph, default_models
    from ida.agent.mcp_client import mcp_tools
    async with mcp_tools() as tools:
        yield build_graph(default_models(tools), tools)


class AgentRuntime:
    def __init__(self, open_graph: Callable[[], AbstractAsyncContextManager] = open_agent_graph,
                 start_timeout_s: float = 180):
        self.loop = asyncio.new_event_loop()
        threading.Thread(target=self.loop.run_forever, daemon=True, name="agent-loop").start()
        self.graph = None
        self._stop: asyncio.Event | None = None
        ready = threading.Event()
        self._serving = asyncio.run_coroutine_threadsafe(self._serve(open_graph, ready), self.loop)
        if not ready.wait(start_timeout_s) or self.graph is None:
            try:   # _serve sets `ready` in its finally block, just before its future completes
                error = self._serving.exception(5)
            except TimeoutError as e:
                error = e
            self.close()
            raise RuntimeError(f"Agent tools failed to start: {error}") from error

    async def _serve(self, open_graph, ready: threading.Event) -> None:
        self._stop = asyncio.Event()
        try:
            async with open_graph() as graph:
                self.graph = graph
                ready.set()
                await self._stop.wait()
        finally:
            ready.set()

    async def _run(self, question: str, out: queue.Queue, tags: list[str]) -> None:
        try:
            # the Langfuse context uses contextvars, so it is entered on the loop, in the run's own task
            with traced_run(question, tags=tags) as trace:
                out.put(("trace", trace.url))
                async for chunk in self.graph.astream({"question": question}, stream_mode="updates",
                                                      config=trace.config):
                    for node, update in chunk.items():
                        out.put(("update", node, update or {}))   # a node that changes nothing streams None
        except Exception as e:  # noqa: BLE001 -- shown in the UI as a failed run
            out.put(("error", f"Run failed: {type(e).__name__}: {str(e)[:300]}"))
        finally:
            flush()
            out.put(_DONE)

    def stream(self, question: str, tags: list[str] | None = None) -> Iterator[tuple]:
        """Yields ("trace", url | None), then ("update", node, update) per node, or ("error", message)."""
        out: queue.Queue = queue.Queue()
        asyncio.run_coroutine_threadsafe(self._run(question, out, tags or ["ui"]), self.loop)
        while (item := out.get()) is not _DONE:
            yield item

    def close(self) -> None:
        if self._stop is not None:
            self.loop.call_soon_threadsafe(self._stop.set)
            with contextlib.suppress(Exception):   # shutting down anyway
                self._serving.result(30)
        self.loop.call_soon_threadsafe(self.loop.stop)
