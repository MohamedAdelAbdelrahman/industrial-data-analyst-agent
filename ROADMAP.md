# Build roadmap (2 weeks × ~15 h)

Every step ends with a commit and push. Tick the box when its "done when" check passes.

## Week 1: data and tools

- [x] **Step 0: Repo and data generator** (1 h)
  Done when: the repo exists, `data/generate_db.py` builds `sensors.db`, and the design doc is written.

- [ ] **Step 1: Environment** (1 h)
  Install Python 3.11 (`uv python install 3.11`, then `uv venv` and `uv pip install -e ".[dev]"`).
  Done when: `pytest` runs (no tests yet) and `python data/generate_db.py` works inside the venv.

- [ ] **Step 2: Manuals corpus** (2 h)
  Write 6 short synthetic manuals (one per machine type) as Markdown and convert them to PDF. Each has specs, the alarm thresholds that match `schema.sql`, a troubleshooting table and a maintenance schedule. Synthetic manuals avoid copyright problems and stay consistent with the database.
  Done when: `data/manuals/*.pdf` exists and each manual has at least 1 troubleshooting entry per sensor type.

- [ ] **Step 3: MCP server and `query_database`** (4 h)
  `src/ida/mcp_server/`: FastMCP app, `SQLGenerator` interface (fine-tuned model plus LLM fallback), sqlglot validator, read-only executor, dataset cache.
  Done when: unit tests cover the validator (rejects DELETE/PRAGMA/ATTACH/multi-statement), the read-only connection, the timeout and truncation. The tool can be called from MCP Inspector.

- [ ] **Step 4: `search_manuals`** (3 h)
  Ingestion script (PyMuPDF → chunks → bge-small → Chroma) plus the tool with a machine_type filter.
  Done when: 10 hand-written queries return the right manual page in the top 3 (recall@3 ≥ 0.8).

- [ ] **Step 5: `run_python` sandbox** (3 h)
  `sandbox/Dockerfile` and a runner with the limits from the design doc. Datasets are mounted and PNGs are collected.
  Done when: tests show that network access fails, `while True` is killed at the timeout, writing outside /tmp and /out fails, and a matplotlib plot comes back.

## Week 2: agent, UI and evaluation

- [ ] **Step 6: LangGraph agent** (5 h)
  State, planner, executor, step_check, replanner, answerer, connected to MCP through `langchain-mcp-adapters`, with budgets.
  Done when: 5 sample questions (one per category) are answered end-to-end from the CLI.

- [ ] **Step 7: Langfuse tracing** (1 h)
  Self-host with docker compose or use the cloud free tier. Add the callback handler and task tags.
  Done when: a run shows one trace per question with node and tool spans plus cost.

- [ ] **Step 8: Guardrails** (2 h)
  input_guard, output_guard, untrusted-text wrapping, budgets. Add adversarial tests: "drop the alarms table", a manual with an injected instruction, an off-topic question.
  Done when: all adversarial tests are blocked or handled.

- [ ] **Step 9: Streamlit UI** (3 h)
  Chat on the left. On the right, live steps: the plan, each tool call (SQL shown, manual hits, plots) and cost/steps counters, via `graph.stream()`. Add a link to the Langfuse trace.
  Done when: the demo questions run from the UI.

- [ ] **Step 10: Evaluation set and harness** (4 h)
  30–40 tasks in `eval/tasks.yaml` plus `eval/run_eval.py` → `eval/results/<date>.json` and a markdown table. Run 2 configs, for example fine-tuned SQL vs LLM SQL, or a big vs small planner.
  Done when: the results table is in the README.

- [ ] **Step 11: Polish** (2 h)
  README with an architecture diagram, demo GIF, results, design decisions and limitations. Add a CV bullet with real numbers.

**Total ≈ 31 h.** If time runs short, cut these first: the second eval config, then output_guard's number check.
