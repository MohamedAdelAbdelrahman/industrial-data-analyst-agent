`arch_v3_planner` vs `arch_v3_single`, 37 tasks (`eval/tasks.yaml`).

### `SQL_BACKEND=llm`

| | `arch_v3_planner` (planner) | `arch_v3_single` (single) |
|---|---|---|
| **passed** | 36/37 (97%) | 37/37 (100%) |
| sql_only | 13/13 (100%) | 13/13 (100%) |
| manual_only | 6/6 (100%) | 6/6 (100%) |
| sql+manual | 6/6 (100%) | 6/6 (100%) |
| sql+python | 4/5 (80%) | 5/5 (100%) |
| multi_hop | 3/3 (100%) | 3/3 (100%) |
| should_refuse | 4/4 (100%) | 4/4 (100%) |
| partial (a budget ended the run) | 0 | 0 |
| avg tool calls | 1.9 | 1.9 |
| avg LLM calls | 5.1 | 2.6 |
| avg agent tokens | 8,270.9 | 6,203.0 |
| avg latency (s) | 17.9 | 10.3 |
| avg SQL-model tokens | 1,112 | 1,218 |
| avg cost per task | $0.0044 | $0.0039 |
| LLM calls per model | gemini-3.5-flash-lite 150, gemini-3.1-flash-lite 17, gemini-3-flash-preview 9, gemini-3.6-flash 7, gemma-4-26b-a4b-it 5 | gemini-3.5-flash-lite 69, gemini-3.6-flash 15, gemini-3-flash-preview 11 |

Tasks with different results (failed checks, `-` = passed):

- `py_compressor03_daily_max`: arch_v3_planner numbers; arch_v3_single -

Cost: what the run would cost at Gemini paid-tier prices (`eval/prices.yaml`, checked 2026-11-13); the project itself runs on the free tier for $0. The fine-tuned SQL model runs locally and costs nothing. "agent only" / `*`: the run was saved before `query_database` reported the SQL model's tokens (`?`), so Gemini's SQL calls are missing from the cost.
