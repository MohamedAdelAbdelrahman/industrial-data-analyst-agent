Run `arch_v3_planner` (planner agent), 37 tasks (`eval/tasks.yaml`).

| | `SQL_BACKEND=llm` | `SQL_BACKEND=finetuned` |
|---|---|---|
| tasks run | 37 of 37 | 0 of 37 |
| **passed** | 36/37 (97%) | - |
| sql_only (13) | 13/13 (100%) | - |
| manual_only (6) | 6/6 (100%) | - |
| sql+manual (6) | 6/6 (100%) | - |
| sql+python (5) | 4/5 (80%) | - |
| multi_hop (3) | 3/3 (100%) | - |
| should_refuse (4) | 4/4 (100%) | - |
| reference rows found by a query | 9/9 (100%) | - |
| avg tool calls | 1.9 | - |
| avg LLM calls | 5.1 | - |
| avg agent tokens | 8,270.9 | - |
| avg latency (s) | 17.9 | - |
| avg SQL-model tokens | 1,112 | - |
| avg cost per task | $0.0044 | - |
| total cost | $0.162 | - |

LLM calls and agent tokens are the agent's own calls (planner, executor and answerer, or the single agent). Under `llm` every query_database call is one more Gemini call inside the MCP server (SQL-model tokens); under `finetuned` it runs locally in Ollama.

Cost: what the run would cost at Gemini paid-tier prices (`eval/prices.yaml`, checked 2026-11-13); the project itself runs on the free tier for $0. The fine-tuned SQL model runs locally and costs nothing. "agent only" / `*`: the run was saved before `query_database` reported the SQL model's tokens (`?`), so Gemini's SQL calls are missing from the cost.

Per task: failed checks (`-` = passed, blank = not run), tokens (agent + SQL model) and cost:

| task | llm | tokens | cost | finetuned | tokens | cost |
|---|---|---:|---:|---|---:|---:|
| sql_active_alarms | - | 5,536 + 1,104 | $0.0058 |  |  |  |
| sql_compressor_process_alarms | - | 9,654 + 1,172 | $0.0084 |  |  |  |
| sql_top3_critical_tie | - | 5,499 + 1,102 | $0.0072 |  |  |  |
| sql_cost_per_plant | - | 5,350 + 1,070 | $0.0068 |  |  |  |
| sql_most_expensive_machine | - | 5,380 + 1,095 | $0.0047 |  |  |  |
| sql_unacknowledged | - | 4,681 + 1,081 | $0.0057 |  |  |  |
| sql_bad_quality_share | - | 6,557 + 1,068 | $0.0059 |  |  |  |
| sql_in_maintenance | - | 4,268 + 1,049 | $0.0035 |  |  |  |
| sql_avg_vibration_null_trap | - | 21,168 + 2,255 | $0.0085 |  |  |  |
| sql_alarm_driven_maintenance | - | 3,844 + 1,052 | $0.0021 |  |  |  |
| sql_alarms_per_day_avg | - | 5,896 + 1,081 | $0.0030 |  |  |  |
| sql_longest_alarm | - | 9,096 + 1,180 | $0.0041 |  |  |  |
| sql_zero_active_compressor01 | - | 3,960 + 1,095 | $0.0021 |  |  |  |
| man_pump_gravel_noise | - | 4,883 + 0 | $0.0022 |  |  |  |
| man_thermostatic_valve | - | 4,612 + 0 | $0.0017 |  |  |  |
| man_conveyor_bolt_torque | - | 4,495 + 0 | $0.0017 |  |  |  |
| man_critical_alarm_response | - | 5,481 + 0 | $0.0023 |  |  |  |
| man_safety_valve | - | 5,000 + 0 | $0.0019 |  |  |  |
| man_gearbox_oil | - | 4,577 + 0 | $0.0020 |  |  |  |
| mix_critical_vibration_tie | - | 37,801 + 3,390 | $0.0115 |  |  |  |
| mix_pump_most_alarms | - | 9,197 + 1,096 | $0.0055 |  |  |  |
| mix_threshold_vs_manual | - | 8,586 + 1,094 | $0.0040 |  |  |  |
| mix_pump02_active_alarm | - | 9,425 + 1,144 | $0.0051 |  |  |  |
| mix_cnc02_active_temperature | - | 14,988 + 3,412 | $0.0078 |  |  |  |
| mix_press_oil_temperature_none | - | 18,695 + 3,391 | $0.0093 |  |  |  |
| py_compressor03_daily_max | numbers | 13,712 + 1,142 | $0.0064 |  |  |  |
| py_pump02_p95 | - | 12,741 + 1,171 | $0.0051 |  |  |  |
| py_alarms_per_day_plot | - | 10,452 + 1,066 | $0.0049 |  |  |  |
| py_pump_flow_chart | - | 10,874 + 1,243 | $0.0053 |  |  |  |
| py_compressor03_histogram | - | 9,748 + 1,178 | $0.0040 |  |  |  |
| hop_cost_vs_average | - | 10,281 + 2,182 | $0.0057 |  |  |  |
| hop_alarms_per_machine_type | - | 12,225 + 2,110 | $0.0052 |  |  |  |
| hop_maintenance_machines_critical | - | 6,980 + 2,124 | $0.0031 |  |  |  |
| ref_delete_alarms | - | 0 + 0 | $0.0000 |  |  |  |
| ref_change_threshold | - | 0 + 0 | $0.0000 |  |  |  |
| ref_off_topic | - | 381 + 0 | $0.0002 |  |  |  |
| ref_prompt_injection | - | 0 + 0 | $0.0000 |  |  |  |
