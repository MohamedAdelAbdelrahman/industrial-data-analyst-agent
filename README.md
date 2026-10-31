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

## Try it
```bash
python -m ida.agent.run "Which pump had the most critical alarms and what does the manual say?"
python -m ida.agent.run --demo                              # 5 sample questions, step by step
streamlit run src/ida/ui/app.py                              # chat UI with live agent steps
python data/generate_db.py --out data/sensors.db            # build the DB once
python -m ida.mcp_server.manuals_index                       # build the manual search index once
python -m ida.mcp_server.sandbox --build                     # build the run_python Docker image once
python scripts/try_mcp_tools.py                              # start MCP server, call both tools
python scripts/try_mcp_tools.py sql "Average spindle temperature per CNC mill last week"
python scripts/try_mcp_tools.py manuals "pump cavitation noise" Pump
python scripts/try_mcp_tools.py chain                        # SQL -> dataset -> sandboxed plot
python eval/eval_retrieval.py                                # retrieval recall@k / MRR
pytest            # unit tests (no API calls)
pytest --live     # also runs tests that call the LLM
```

## Data
`data/generate_db.py` builds a synthetic but internally consistent database: alarms are derived from readings and thresholds, and corrective maintenance is linked to critical alarms. It also contains deliberate SQL traps such as NULL readings, active alarms and a decommissioned machine.

```bash
python data/generate_db.py --out data/sensors.db --seed 42 --days 60
python data/build_manuals.py      # -> data/manuals/*.pdf (committed)
```

The manuals are synthetic. Their alarm thresholds come from the same profiles as the database, and they include troubleshooting tables, alarm-response procedures, maintenance intervals and part numbers.

The question→SQL pairs in `finetune/data/` were written by Gemini and checked by execution. They are for private learning: the terms of the Gemini API limit using it to develop competing models, so this dataset and any model trained on it are not published.

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
