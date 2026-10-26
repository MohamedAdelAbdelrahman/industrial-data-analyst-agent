# Industrial Data Analyst Agent

A LangGraph planner/executor agent that answers questions about an industrial machine fleet. It uses tools served by its own **MCP server**:

| Tool | What it does |
|---|---|
| `query_database` | Natural language → **fine-tuned Text-to-SQL model** → validated, read-only SQL on a SQLite sensor DB |
| `search_manuals` | Retrieval over machine-manual PDFs (page-level citations) |
| `run_python` | Analysis and plots in a Docker sandbox (no network, resource limits, timeout) |

The project also covers Langfuse tracing, layered guardrails, a Streamlit UI that shows each agent step, and an evaluation set of 30–40 tasks that measures success rate, steps and cost.

> 🚧 Work in progress. See [ROADMAP.md](ROADMAP.md) for progress and [docs/design.md](docs/design.md) for the architecture.

## Example questions
- "Which compressor had the most critical alarms in September, and what does the manual recommend for that alarm?"
- "Plot the spindle temperature of CNC-Mill-03 around its last HIGH_HIGH alarm."
- "What was the total corrective maintenance cost per plant, and which machine type drives it?"

## Setup
Requires Python ≥ 3.11 and Docker (for `run_python`).

```bash
py -3.12 -m venv .venv            # or: python3.12 -m venv .venv
.venv\Scripts\activate            # Linux/macOS: source .venv/bin/activate
pip install -e ".[all]"           # extras: rag, sandbox, obs, ui, dev
pytest
```

## Data
`data/generate_db.py` builds a synthetic but internally consistent database: alarms are derived from readings and thresholds, and corrective maintenance is linked to critical alarms. It also contains deliberate SQL traps such as NULL readings, active alarms and a decommissioned machine.

```bash
python data/generate_db.py --out data/sensors.db --seed 42 --days 60
```

## Layout (target)
```
data/            schema, DB generator, manuals
src/ida/
  mcp_server/    FastMCP server + tools
  agent/         LangGraph graph, prompts, guardrails
  ui/            Streamlit app
sandbox/         Dockerfile for run_python
eval/            tasks.yaml, harness, results
tests/
```
