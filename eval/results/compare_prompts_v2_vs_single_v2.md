`prompts_v2` vs `single_v2`, 37 tasks (`eval/tasks.yaml`).

### `SQL_BACKEND=llm`

| | `prompts_v2` (planner) | `single_v2` (single) |
|---|---|---|
| **passed** | 36/37 (97%) | 37/37 (100%) |
| sql_only | 13/13 (100%) | 13/13 (100%) |
| manual_only | 6/6 (100%) | 6/6 (100%) |
| sql+manual | 5/6 (83%) | 6/6 (100%) |
| sql+python | 5/5 (100%) | 5/5 (100%) |
| multi_hop | 3/3 (100%) | 3/3 (100%) |
| should_refuse | 4/4 (100%) | 4/4 (100%) |
| partial (a budget ended the run) | 0 | 0 |
| avg tool calls | 1.7 | 1.8 |
| avg LLM calls | 4.8 | 2.5 |
| avg tokens | 7,223.0 | 6,221.9 |
| avg latency (s) | 19.2 | 21.0 |
| LLM calls per model | gemini-3.5-flash-lite 115, gemini-3.1-flash-lite 44, gemini-3-flash-preview 11, gemini-3.6-flash 9 | gemini-3.5-flash-lite 56, gemini-3-flash-preview 19, gemini-3.6-flash 17 |

Tasks with different results (failed checks, `-` = passed):

- `mix_press_oil_temperature_none`: prompts_v2 mention; single_v2 -
