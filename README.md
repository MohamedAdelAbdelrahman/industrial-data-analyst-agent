# Industrial Data Analyst Agent

An agent that answers questions about an industrial machine fleet (27 machines, 60 days of sensor readings, alarms and maintenance) by combining **SQL over a sensor database**, **search over machine manuals** and **Python analysis in a sandbox**. The SQL can come from a **1.5B model I fine-tuned** (QLoRA, served locally by Ollama) or from Gemini; a 37-task eval shows both reach the same success rate inside the agent.

**Headline numbers**
- Fine-tuning Qwen2.5-Coder-1.5B raised Text-to-SQL execution accuracy from **28.7% to 64.3%** on 129 held-out questions (Gemini flash-lite: 76.7%).
- The full agent passes **35/37** eval tasks with Gemini writing the SQL and **34/37** with the fine-tuned local model, after two measured rounds of prompt changes (baseline 33 and 32).
- In a same-day, task-by-task comparison, a **single tool-calling agent** (no plan) passes **37/37** against 36/37 for planner/executor, with half the LLM calls, half the median latency (3.9 s vs 8.0 s) and 11% lower cost ($0.0039 vs $0.0044 per task at paid-tier prices).
- Built for **$0**: free-tier Gemini with per-role model pools and a quota memory, a free Colab T4 for training, and local inference.

![Demo: the Streamlit UI answers a question with SQL and a manual search, and shows the plan, tool calls and SQL](docs/demo.gif)

*Recorded live on the free Gemini tier (26 s; slower runs happen when a model is busy and the pool switches models).*

## Architecture

```mermaid
flowchart LR
    U(["Question"]) --> IG["input_guard"]
    IG -->|blocked| X(["Refusal"])
    IG --> P["planner"]
    P -->|off-topic| X
    P --> E["executor"]
    E -->|tool call| T["MCP tools"]
    T --> E
    E --> SC["step_check"]
    SC -->|next step| E
    SC -->|step failed| R["replanner"]
    R --> E
    SC -->|done or budget hit| A["answerer"]
    R --> A
    A --> OG["output_guard"]
    OG --> F(["Answer with evidence"])

    subgraph MCP ["Own MCP server"]
        Q["query_database: question to SQL, validated, read-only SQLite"]
        S["search_manuals: PDF pages, bge-small, Chroma"]
        Y["run_python: Docker, no network, 512 MB, non-root"]
    end
    T --> Q
    T --> S
    T --> Y
    Q -.->|SQL_BACKEND=llm| G["Gemini"]
    Q -.->|SQL_BACKEND=finetuned| O["Fine-tuned Qwen 1.5B, Ollama, 4-bit GGUF"]
```

- **Agent**: LangGraph planner/executor with a replanner and budgets (15 tool calls, 4 per step, 2 replans, 25 LLM calls). The executor works one plan step at a time.
- **Tools**: one MCP server the agent reaches through `langchain-mcp-adapters`. `query_database` takes natural language, not SQL, so the fine-tuned model does the SQL work. Large query results stay on the server as datasets that `run_python` mounts read-only, so they never pass through the LLM context.
- **Guardrails**: an input guard that blocks write and injection requests with 0 LLM calls, an SQL AST allow-list on a read-only connection, every tool result wrapped as `<untrusted_data>`, the Docker sandbox, and an output guard that flags numbers not found in any tool result.
- **Observability**: Langfuse traces each question with nested spans for nodes, LLM calls (tokens, cost) and tool calls.
- **UI**: Streamlit chat that streams each agent step, the SQL, manual citations and plots.

Full interfaces and the decision log: [docs/design.md](docs/design.md).

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

**Manual search** (`eval/eval_retrieval.py`, 17 queries): recall@1 0.88, recall@3 0.94, MRR 0.91.

**The whole agent** (`eval/run_eval.py`, 37 tasks in `eval/tasks.yaml`, checked without an LLM judge). Both backends ran each task back to back on the same free-tier Gemini planner and executor models; only the model that writes the SQL differs:

| | Gemini writes SQL | Fine-tuned Qwen writes SQL |
|---|---|---|
| **Tasks passed** | **33/37 (89%)** | **32/37 (86%)** |
| sql_only (13) | 11 | 12 |
| manual_only (6) | 6 | 6 |
| sql+manual (6) | 5 | 5 |
| sql+python (5) | 4 | 4 |
| multi_hop (3) | 3 | 1 |
| should_refuse (4) | 4 | 4 |
| Avg tool calls / LLM calls per task | 1.6 / 4.8 | 2.1 / 5.5 |
| Avg latency | 23 s | 40 s |
| Gemini calls for SQL | 1 per query | 0 (local) |

Inside the agent, the 1.5B local model is level with Gemini: a 1-task gap on 37 tasks is noise. It writes worse SQL on its own (64% vs 77%), but the planner and executor catch part of that with retries, which is why its runs take more tool calls and longer. Both backends fail the same planted trap: when 5 machines tie for "top 3", they answer with `LIMIT 3` and name only 3. Every failed task was read by hand. (These numbers are one task lower than first reported: a stricter check on the Compressor-03 daily-maximum task, explained below, turned a false pass into a fail on both backends.) Details: [`eval/results/baseline/report.md`](eval/results/baseline/report.md) and the decision log in [docs/design.md](docs/design.md).

**Prompt changes, measured** (same 37 tasks, each change as a new run against the baseline). The SQL prompt never changed, because it is the fine-tuned model's training format; only the planner, executor and answerer prompts did.

| | Gemini writes SQL | Fine-tuned Qwen writes SQL |
|---|---|---|
| baseline | 33/37 | 32/37 |
| `prompts_v1`: names not IDs, full rankings instead of `LIMIT` (ties), separate questions per table | 35/37 | 33/37 (4 fixed, 3 broken) |
| **`prompts_v2`**: plain-word questions, sensor types, aggregates in the database, 60-day rates | **35/37** | **34/37** |
| avg latency in v2 | 19 s | 19 s |

v1 helped the Gemini backend but not the fine-tuned one: its more detailed questions ("ask for sensor names and the filtered columns") made the 1.5B model invent a `sensor_name` column and drop filters. v2 asks in plain words instead, and the fine-tuned backend fixed all 3 of those. Both ties are now answered in full on both backends.

Still failing in v2: both backends report the wrong peak for "plot the daily maximum discharge temperature of Compressor-03" (95.10 °C instead of 95.75): the query returns all 60 days, but the answerer's evidence shows only the first 20 rows of each result and the peak is on day 37. The default 1% number tolerance let 95.10 pass until a hand check found it, so this task now uses 0.1%. Both backends also answer "which presses had a HIGH_HIGH oil-temperature alarm" (true answer: none) with presses whose HIGH_HIGH alarms were on other sensors, after the exact query correctly returned no rows; and the fine-tuned model once dropped the machine column from a per-pump query, so the chart step ran out of tool calls. Each task ran once per config, so a 1-2 task difference is close to noise. Reports: [`baseline`](eval/results/baseline/report.md), [`prompts_v1`](eval/results/prompts_v1/report.md), [`prompts_v2`](eval/results/prompts_v2/report.md).

**Single agent vs planner/executor** (`--arch both`, run `arch_v3`, Gemini writes the SQL). The single agent is one model from the planner pool with all three tools in a ReAct loop: same tools, guards, budgets and query/answer rules, but no plan, no separate executor and no separate answerer. Both graphs ran each task back to back on the same day, so both saw the same free-tier models. Cost is what the same tokens would cost at Gemini paid-tier prices (`eval/prices.yaml`); the project itself runs for $0.

| | planner/executor | single agent |
|---|---|---|
| **Tasks passed** | **36/37** | **37/37** |
| Avg LLM calls per task | 5.1 | 2.6 |
| Avg tool calls per task | 1.9 | 1.9 |
| Avg tokens per task (agent + SQL model) | 9,383 | 7,421 |
| Median latency | 8.0 s | 3.9 s |
| Avg cost per task | $0.0044 | $0.0039 |

The one task that differs is the Compressor-03 daily maximum: the planner's answerer sees only the first 20 rows of a query result and misses the peak on day 37; the single agent works with up to 50 rows and computed the maximum in Python. Every answer of both runs was read by hand. Most tasks need 1-3 tool calls, and for those a separate plan, executor and answerer double the LLM calls. The cost gap is much smaller than the call gap, though: the planner's executor runs on the cheap flash-lite, while every single-agent turn runs on the planner pool's stronger, pricier models. What the split still gives: a plan the UI can show, per-step budgets and replanning, and a cheap model for the tool loop. Each task ran once, so a 1-task gap is noise. Side by side, with per-model call counts: [`compare_arch_v3_planner_vs_arch_v3_single.md`](eval/results/compare_arch_v3_planner_vs_arch_v3_single.md); the first, not interleaved comparison (9 days apart): [`compare_prompts_v2_vs_single_v2.md`](eval/results/compare_prompts_v2_vs_single_v2.md).

## Design decisions
Each of these came from a failure seen while building; the [decision log](docs/design.md#6-decision-log-findings-while-building) has the details.

- **Training data checked by execution, then by reading results.** Gemini wrote about 1,500 question/SQL pairs; each one had to run and return a non-trivial result. A hand review then found that `ORDER BY ... LIMIT 1` hides ties, which no SQL reading catches, so the checker reruns each query to detect ties, mixed units (ranking rpm against °C) and bare columns beside `MAX()`.
- **No leakage between train and test.** The split hashes the SQL shape, and a fingerprint of tables, literals and result rows removed 9 test questions that repeated a training query with the joins in another order.
- **Deterministic eval, no LLM judge.** Each task has a reference SQL, a number, required manual pages or "must refuse". Both SQL backends run each task back to back so they see the same rotated Gemini models. Every failure is read by hand before it is reported.
- **Prompt changes measured, not assumed.** A rule that helped Gemini ("ask for sensor names and the filtered columns") broke 4 tasks for the 1.5B model, which invented a `sensor_name` column. For a weak SQL model, the executor has to ask simpler questions, not more precise ones. The SQL prompt never changed, because it is the fine-tuned model's training format.
- **Free tier as a design constraint.** Per-role model pools with a file-based quota memory skip models until their reset time and back off exponentially on server errors, so a run moves to the next model instead of failing.

## Limitations
- The data and manuals are synthetic, and Gemini models wrote the gold SQL, so Gemini's 76.7% is an upper estimate.
- 37 tasks, each run once per config: a difference of 1-2 tasks is noise.
- The architecture comparison ran each task once on 37 mostly short tasks (1-3 tool calls); harder multi-step tasks are where a plan could pay off, and they are not in the set.
- The planner's answerer sees only the first 20 rows of each query result, so a peak or total outside them is missed unless the executor computed it (the Compressor-03 daily maximum fails this way).
- When the exact query correctly returns no rows, the agent can widen it and report the wider rows as matches (the HIGH_HIGH oil-temperature task fails this way on both backends).
- `output_guard` catches invented numbers, not wrongly summarised ones or wrong claims.
- The fine-tuned model's remaining SQL errors are mostly invented column names; it is weak on window functions, thresholds and time windows.
- The training pairs were written with Gemini, whose terms limit developing competing models, so the dataset and the trained model are not published.

## Run with Docker
Needs Docker (Desktop) and a free [Google AI Studio](https://aistudio.google.com) API key.

```bash
cp .env.example .env              # Windows: copy; set GOOGLE_API_KEY (Langfuse keys optional)
docker compose up --build         # first start: builds images, the database and the manual index
```

Open http://localhost:8501. Data, the search index and the quota memory live in the `ida-data` volume, so later starts are fast; `docker compose down -v` deletes them.

- **Fine-tuned SQL model**: put the GGUF in `finetune/model/` (see below), set `SQL_BACKEND=finetuned` in `.env`, then `docker compose --profile finetuned up`. This adds an Ollama container and registers the model as `ida-sql`. That container runs the model on CPU (about 45 s per SQL query in a 4 GB Docker VM). An Ollama that already runs on the host with a GPU is much faster (about 1 s): set `DOCKER_OLLAMA_URL=http://host.docker.internal:11434` in `.env` and start without the profile.
- **`run_python` in Compose**: the app container gets the host's Docker socket so it can start a fresh sandbox container per run. The sandbox still has no network, no socket, a read-only root and 512 MB. Because the Docker daemon resolves mount paths on the host, the run folders sit on the named volume `ida-sandbox-work`, and each sandbox mounts only its own subfolder ([sandbox.py](src/ida/mcp_server/sandbox.py)).

## Setup (local Python)
Requires Python ≥ 3.11, a free [Google AI Studio](https://aistudio.google.com) API key, and Docker (only for `run_python`).

```bash
py -3.12 -m venv .venv            # or: python3.12 -m venv .venv
.venv\Scripts\activate            # Linux/macOS: source .venv/bin/activate
pip install -e ".[all]"           # extras: rag, sandbox, obs, ui, dev
copy .env.example .env            # Linux/macOS: cp; then set GOOGLE_API_KEY (Langfuse keys optional)

# build once
python data/generate_db.py --out data/sensors.db   # sensor database (seed 42, 60 days)
python -m ida.mcp_server.manuals_index              # manual search index
python -m ida.mcp_server.sandbox --build            # run_python Docker image
pytest                                              # unit tests, no API calls
```

**Fine-tuned SQL backend (optional).** Train with `finetune/train_colab.ipynb` on a free Colab T4, put the exported `Qwen2.5-Coder-1.5B-Instruct.Q4_K_M.gguf` in `finetune/model/`, then:

```bash
ollama create ida-sql -f finetune/Modelfile
# in .env: SQL_BACKEND=finetuned   (the MCP server reads .env, a shell variable does not reach it)
```

## Try it
```bash
streamlit run src/ida/ui/app.py                              # chat UI with live agent steps
python -m ida.agent.run "Which pump had the most critical alarms and what does the manual say?"
python -m ida.agent.run --demo                              # 5 sample questions, step by step
python scripts/try_mcp_tools.py                              # start MCP server, call both tools
python scripts/try_mcp_tools.py sql "Average spindle temperature per CNC mill last week"
python scripts/try_mcp_tools.py manuals "pump cavitation noise" Pump
python scripts/try_mcp_tools.py chain                        # SQL -> dataset -> sandboxed plot
python -m ida.model_pool                                     # Gemini quota status per model
```

## Evaluate
```bash
python eval/eval_retrieval.py                                # manual search recall@k / MRR
python eval/eval_sql.py                                      # Text-to-SQL execution accuracy
python eval/run_eval.py --config both --run my_run           # 37 agent tasks, both SQL backends
python eval/run_eval.py --arch both --config llm --run my_arch    # planner and single agent, task by task -> my_arch_planner, my_arch_single
python eval/run_eval.py --arch single --config llm --run my_single   # single agent instead of planner/executor
python eval/run_eval.py --compare my_run my_single           # side by side -> eval/results/compare_*.md
pytest --live                                                # also runs tests that call the LLM
```

## Data
`data/generate_db.py` builds a synthetic but internally consistent database: alarms are derived from readings and thresholds, and corrective maintenance is linked to critical alarms. It also contains deliberate SQL traps such as NULL readings, active alarms and a decommissioned machine.

```bash
python data/generate_db.py --out data/sensors.db --seed 42 --days 60
python data/build_manuals.py      # -> data/manuals/*.pdf (committed)
```

The manuals are synthetic. Their alarm thresholds come from the same profiles as the database, and they include troubleshooting tables, alarm-response procedures, maintenance intervals and part numbers.

The question→SQL pairs were written by Gemini and checked by execution. They are **not included in this repository**: the terms of the Gemini API limit using it to develop competing models, so the dataset and the model trained on it stay private. The pipeline that builds them is included; see [`finetune/data/README.md`](finetune/data/README.md).

## Layout
```
data/            schema, DB generator, manual builder, manual PDFs
src/ida/
  mcp_server/    FastMCP server, query_database / search_manuals / run_python
  agent/         LangGraph graph (planner/executor; single.py: one-model baseline), prompts, guardrails, tracing, CLI
  ui/            Streamlit app
  model_pool.py  per-role Gemini pools with quota memory
  training_data.py  question/SQL pair generation, checks, split
finetune/        Colab QLoRA notebook, Ollama Modelfile, data (private)
sandbox/         Dockerfile for run_python
eval/            tasks.yaml, harnesses, results per run
tests/           207 unit tests (no API calls, run on every push); --live for LLM tests
.github/         CI: ruff + pytest
```
