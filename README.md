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

## Results

**Text-to-SQL on its own** (`eval/eval_sql.py`, 129 held-out test questions; a question counts as right when the query returns the same rows as the gold SQL):

| Model | Execution accuracy |
|---|---|
| Qwen2.5-Coder-1.5B, base | 28.7% |
| Qwen2.5-Coder-1.5B, QLoRA fine-tuned (served by Ollama, 4-bit GGUF) | 64.3% |
| gemini-3.1-flash-lite | 76.7% (upper estimate: Gemini models wrote the gold SQL) |

**The whole agent** (`eval/run_eval.py`, 37 tasks in `eval/tasks.yaml`, checked without an LLM judge). Both backends ran each task back to back on the same free-tier Gemini planner and executor models; only the model that writes the SQL differs:

| | Gemini writes SQL | Fine-tuned Qwen writes SQL |
|---|---|---|
| **Tasks passed** | **34/37 (92%)** | **33/37 (89%)** |
| sql_only (13) | 11 | 12 |
| manual_only (6) | 6 | 6 |
| sql+manual (6) | 5 | 5 |
| sql+python (5) | 5 | 5 |
| multi_hop (3) | 3 | 1 |
| should_refuse (4) | 4 | 4 |
| Avg tool calls / LLM calls per task | 1.6 / 4.8 | 2.1 / 5.5 |
| Avg latency | 23 s | 40 s |
| Gemini calls for SQL | 1 per query | 0 (local) |

Inside the agent, the 1.5B local model is level with Gemini: a 1-task gap on 37 tasks is noise. It writes worse SQL on its own (64% vs 77%), but the planner and executor catch part of that with retries, which is why its runs take more tool calls and longer. Both backends fail the same planted trap: when 5 machines tie for "top 3", they answer with `LIMIT 3` and name only 3. Every failed task was read by hand. Details: [`eval/results/baseline/report.md`](eval/results/baseline/report.md) and the decision log in [docs/design.md](docs/design.md).

**Prompt changes, measured** (same 37 tasks, each change as a new run against the baseline). The SQL prompt never changed, because it is the fine-tuned model's training format; only the planner, executor and answerer prompts did.

| | Gemini writes SQL | Fine-tuned Qwen writes SQL |
|---|---|---|
| baseline | 34/37 | 33/37 |
| `prompts_v1`: names not IDs, full rankings instead of `LIMIT` (ties), separate questions per table | 36/37 | 33/37 (4 fixed, 4 broken) |
| **`prompts_v2`**: plain-word questions, sensor types, aggregates in the database, 60-day rates | **36/37** | **35/37** |
| avg latency in v2 | 19 s | 19 s |

v1 helped the Gemini backend but not the fine-tuned one: its more detailed questions ("ask for sensor names and the filtered columns") made the 1.5B model invent a `sensor_name` column and drop filters. v2 asks in plain words instead, and the fine-tuned backend fixed all 4 of those. Both ties are now answered in full on both backends.

Still failing in v2: both backends answer "which presses had a HIGH_HIGH oil-temperature alarm" (true answer: none) with presses whose HIGH_HIGH alarms were on other sensors, after the exact query correctly returned no rows; and the fine-tuned model once dropped the machine column from a per-pump query, so the chart step ran out of tool calls. Each task ran once per config, so a 1-2 task difference is close to noise. Reports: [`baseline`](eval/results/baseline/report.md), [`prompts_v1`](eval/results/prompts_v1/report.md), [`prompts_v2`](eval/results/prompts_v2/report.md).

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
