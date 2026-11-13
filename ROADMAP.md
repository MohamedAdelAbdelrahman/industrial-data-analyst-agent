# Build roadmap (2 weeks × ~15 h)

Every step ends with a commit and push. Tick the box when its "done when" check passes.

## Week 1: data and tools

- [x] **Step 0: Repo and data generator** (1 h)
  Done when: the repo exists, `data/generate_db.py` builds `sensors.db`, and the design doc is written.

- [x] **Step 1: Environment** (1 h)
  Python 3.12 venv (`py -3.12 -m venv .venv`, then `pip install -e ".[all]"`). Extras: rag, sandbox, obs, ui, dev.
  Done when: `pytest` passes (6 DB-consistency smoke tests) and `python data/generate_db.py` works inside the venv.

- [x] **Step 2: Manuals corpus** (2 h)
  `data/build_manuals.py` renders 6 synthetic manuals (one per machine type) to PDF with PyMuPDF; thresholds are imported from `generate_db.PROFILES`. Each has specs, the alarm thresholds that match `schema.sql`, a troubleshooting table and a maintenance schedule. Synthetic manuals avoid copyright problems and stay consistent with the database.
  Done when: `data/manuals/*.pdf` exists and tests confirm thresholds match the DB, every sensor has a troubleshooting section, and part numbers survive text extraction (`ida.manuals.extract_pages`).

- [x] **Step 3: MCP server and `query_database`** (4 h)
  `src/ida/mcp_server/`: FastMCP app, `SQLGenerator` interface (fine-tuned model plus LLM fallback), sqlglot validator, read-only executor, dataset cache.
  Done when: unit tests cover the validator (rejects DELETE/PRAGMA/ATTACH/multi-statement), the read-only connection, the timeout and truncation. `scripts/try_query_database.py` calls the tool over MCP stdio (also works in `mcp dev`).

- [x] **Step 4: `search_manuals`** (3 h)
  Ingestion script (PyMuPDF → chunks → bge-small → Chroma) plus the tool with a machine_type filter.
  Done when: 10 hand-written queries return the right manual page in the top 3 (recall@3 ≥ 0.8).
  Result: 17 queries (`eval/eval_retrieval.py`): recall@1 0.88, recall@3 0.94, MRR 0.91. See the decision log in docs/design.md.

- [x] **Step 5: `run_python` sandbox** (3 h)
  `sandbox/Dockerfile` and a runner with the limits from the design doc. Datasets are mounted and PNGs are collected.
  Done when: tests show that network access fails, `while True` is killed at the timeout, writing outside /tmp and /out fails, and a matplotlib plot comes back.

## Week 2: agent, UI and evaluation

- [x] **Step 6: LangGraph agent** (5 h)
  State, planner, executor, step_check, replanner, answerer, connected to MCP through `langchain-mcp-adapters`, with budgets.
  Done when: 5 sample questions (one per category) are answered end-to-end from the CLI (`python -m ida.agent.run --demo`).
  Result: 4/5 correct plus the refusal; Q4 misses a tie (kept as a baseline failure for the eval).

- [x] **Step 6b: Fine-tune the Text-to-SQL model** (6 h, split across a and b below)
  Until this step, `query_database` uses Gemini to write SQL (`SQL_BACKEND=llm`).
  - **a. Training data** (2 h, local): Gemini (free tier) writes about 1,500 question→SQL pairs for `schema.sql` covering joins, aggregates, time windows and the NULL/active-alarm traps. **Every SQL is executed** on `sensors.db` and only pairs that run and return rows are kept. A held-out test split of about 150 pairs is never trained on. Output: `finetune/data/{train,test}.jsonl`.
  - **b. Training** (3 h, Google Colab free T4 GPU): QLoRA with Unsloth on `Qwen2.5-Coder-1.5B-Instruct` from Hugging Face. Notebook: `finetune/train_colab.ipynb`. Export a GGUF (q4_k_m) and download it.
  - **c. Serving** (1 h, local): `ollama create ida-sql -f finetune/Modelfile`, then set `SQL_BACKEND=finetuned`.
  Done when: execution accuracy on the test split is reported for base Qwen, fine-tuned Qwen and Gemini.
  Result (a): done. `ida.training_data` generates, checks and splits the pairs (55 tests in `tests/test_training_data.py`). 1,518 pairs (1,380 train, 129 test, 1,282 distinct SQL shapes, 9 test repeats dropped) in `finetune/data/`. To add data: `python -m ida.training_data generate --max-requests 60`, then `split`.
  Progress (b): `finetune/train_colab.ipynb` is written. It also answers the test split with the base and fine-tuned models (`predictions.jsonl`), and the GGUF and predictions go to `finetune/model/`, which git ignores. Training and predictions ran on Colab; the GGUF export needed about 8 GB of temporary space, more than Drive had free, so it now runs on Colab's own disk and copies only the final file to Drive. Validation loss per epoch: 0.0403, 0.0358, 0.0342.
  Result (scores): `python eval/eval_sql.py`, 129 test questions, same rows as the gold SQL: base Qwen 37 (28.7%), fine-tuned Qwen 81 (62.8%), gemini-3.1-flash-lite 99 (76.7%). Gemini's score is an upper estimate, because Gemini models wrote the gold SQL.
  Result (c): done. The GGUF served by Ollama (`python eval/eval_sql.py --ask-ollama`) scores 83 of 129 (64.3%), level with the Colab model, and `query_database` runs on it over MCP. `.env` stays on `SQL_BACKEND=llm` until Step 10 compares both in the agent. The MCP server reads `SQL_BACKEND` from `.env`: a value set only in the shell does not reach it.

- [x] **Step 7: Langfuse tracing** (1 h)
  Self-host with docker compose or use the cloud free tier. Add the callback handler and task tags.
  Done when: a run shows one trace per question with node and tool spans plus cost.

- [x] **Step 7b: Model pools and quota memory** (1 h)
  Each role (planner/answerer, executor, SQL) has an ordered pool of free-tier models. Failed models are skipped and remembered in `data/cache/quota.json` until their quota resets. `python -m ida.model_pool` shows calls today and blocked models.
  Done when: unit tests cover fall-over, daily/minute/5xx blocking and the fail-fast path, and a live run survives a model error.

- [x] **Step 8: Guardrails** (2 h)
  input_guard, output_guard, untrusted-text wrapping, budgets. Add adversarial tests: "drop the alarms table", a manual with an injected instruction, an off-topic question.
  Done when: all adversarial tests are blocked or handled.
  Result: `ida.agent.guards` plus `input_guard`/`output_guard` graph nodes and a 25-LLM-call budget; 29 tests in `tests/test_guardrails.py` (write requests and injections blocked before any LLM call, injected manual text wrapped and flagged, off-topic refused by the planner, made-up numbers flagged).

- [x] **Step 9: Streamlit UI** (3 h)
  Chat on the left. On the right, live steps: the plan, each tool call (SQL shown, manual hits, plots) and cost/steps counters, via `graph.stream()`. Add a link to the Langfuse trace.
  Done when: the demo questions run from the UI.
  Result: `src/ida/ui/app.py` (chat + live step panel with SQL, manual hits, plots, counters, trace link) and `ida.ui.runtime` (one MCP session on a background loop, shared across reruns); 4 tests in `tests/test_ui.py`. All 5 demo questions run from the browser.

- [x] **Step 10: Evaluation set and harness** (4 h)
  30–40 tasks in `eval/tasks.yaml` plus `eval/run_eval.py` → `eval/results/<date>.json` and a markdown table. Run 2 configs: `SQL_BACKEND=llm` vs `SQL_BACKEND=finetuned`.
  Done when: the results table is in the README.
  Progress: 37 tasks in `eval/tasks.yaml` (13 sql_only, 6 manual_only, 6 sql+manual, 5 sql+python, 3 multi_hop, 4 should_refuse), with deterministic checks (reference SQL, required strings/numbers, manual section, chart, refusal). `eval/run_eval.py --config both` saves each task as it finishes and continues after a quota stop; `--report` writes `eval/results/<run>/report.md`. 20 checker tests in `tests/test_run_eval.py`.
  Result: baseline run (`eval/results/baseline/`): `llm` 34/37 (92%), `finetuned` 33/37 (89%), level inside the agent; the fine-tuned backend needs 0 Gemini calls for SQL but takes more tool calls (2.1 vs 1.6) and longer (40 s vs 23 s). All failures read by hand (one reference fixed). Table in the README. Next: prompt changes for ties and name-not-ID answers, measured as a new `--run`.
  Prompt changes (`eval/results/prompts_v1/`): `llm` 34 → 36/37, `finetuned` 33 → 33/37 (4 fixed, 4 newly failing, read by hand). `prompts_v2` (plain-word questions, sensor types, aggregates in the database, 60-day rates): `llm` 36/37, `finetuned` 35/37; the 3 remaining failures are read by hand and listed as known limitations in docs/design.md.
  Rescored 2026-11-13 with a stricter check on the Compressor-03 daily maximum (a 95.10 answer passed the 1% tolerance): baseline 33/32, `prompts_v1` 35/33, `prompts_v2` 35/34. See the last rows of the decision log in docs/design.md.

- [x] **Step 11: Polish** (2 h)
  README with an architecture diagram, demo GIF, results, design decisions and limitations. Add a CV bullet with real numbers.
  Result: done. README has the headline numbers, a Mermaid diagram, design decisions, limitations and `docs/demo.gif`; the CV bullet is kept outside the repo.

**Total ≈ 37 h** (about 7 h over budget because of 6b). If time runs short, cut these first: output_guard's number check, then shrink the eval set to 30 tasks, then reduce the fine-tuning data to 800 pairs.
