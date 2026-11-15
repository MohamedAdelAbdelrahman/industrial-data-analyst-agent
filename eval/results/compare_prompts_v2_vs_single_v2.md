`prompts_v2` vs `single_v2`, 37 tasks (`eval/tasks.yaml`).

### `SQL_BACKEND=llm`

| | `prompts_v2` (planner) | `single_v2` (single) |
|---|---|---|
| **passed** | 35/37 (95%) | 37/37 (100%) |
| sql_only | 13/13 (100%) | 13/13 (100%) |
| manual_only | 6/6 (100%) | 6/6 (100%) |
| sql+manual | 5/6 (83%) | 6/6 (100%) |
| sql+python | 4/5 (80%) | 5/5 (100%) |
| multi_hop | 3/3 (100%) | 3/3 (100%) |
| should_refuse | 4/4 (100%) | 4/4 (100%) |
| partial (a budget ended the run) | 0 | 0 |
| avg tool calls | 1.7 | 1.8 |
| avg LLM calls | 4.8 | 2.5 |
| avg agent tokens | 7,223.0 | 6,221.9 |
| avg latency (s) | 19.2 | 21.0 |
| avg SQL-model tokens | not recorded | not recorded |
| avg cost per task | $0.0038 (agent only) | $0.0036 (agent only) |
| LLM calls per model | gemini-3.5-flash-lite 115, gemini-3.1-flash-lite 44, gemini-3-flash-preview 11, gemini-3.6-flash 9 | gemini-3.5-flash-lite 56, gemini-3-flash-preview 19, gemini-3.6-flash 17 |

Tasks with different results (failed checks, `-` = passed):

- `mix_press_oil_temperature_none`: prompts_v2 mention; single_v2 -
- `py_compressor03_daily_max`: prompts_v2 numbers; single_v2 -

Cost: what the run would cost at Gemini paid-tier prices (`eval/prices.yaml`, checked 2026-11-13); the project itself runs on the free tier for $0. The fine-tuned SQL model runs locally and costs nothing. "agent only" / `*`: the run was saved before `query_database` reported the SQL model's tokens (`?`), so Gemini's SQL calls are missing from the cost.
