"""Connect to the industrial-tools MCP server and expose its tools as LangChain tools."""
import os
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from langchain_core.tools import BaseTool
from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_mcp_adapters.tools import load_mcp_tools

SERVER = "industrial"


@asynccontextmanager
async def mcp_tools() -> AsyncIterator[list[BaseTool]]:
    """One server process and session for the whole run (the server takes ~20 s to warm up)."""
    # pass the full environment: the MCP stdio default keeps only a few safe variables, so
    # per-run overrides like SQL_LLM_MODEL=... would silently not reach the server
    client = MultiServerMCPClient({SERVER: {
        "transport": "stdio", "command": sys.executable, "args": ["-m", "ida.mcp_server.server"],
        "env": dict(os.environ)}})
    async with client.session(SERVER) as session:
        yield await load_mcp_tools(session)
