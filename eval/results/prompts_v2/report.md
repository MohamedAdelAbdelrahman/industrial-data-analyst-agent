Run `prompts_v2`, 37 tasks (`eval/tasks.yaml`).

| | `SQL_BACKEND=llm` | `SQL_BACKEND=finetuned` |
|---|---|---|
| tasks run | 37 of 37 | 37 of 37 |
| **passed** | 36/37 (97%) | 35/37 (95%) |
| sql_only (13) | 13/13 (100%) | 13/13 (100%) |
| manual_only (6) | 6/6 (100%) | 6/6 (100%) |
| sql+manual (6) | 5/6 (83%) | 5/6 (83%) |
| sql+python (5) | 5/5 (100%) | 4/5 (80%) |
| multi_hop (3) | 3/3 (100%) | 3/3 (100%) |
| should_refuse (4) | 4/4 (100%) | 4/4 (100%) |
| reference rows found by a query | 7/9 (78%) | 8/9 (89%) |
| avg tool calls | 1.7 | 1.9 |
| avg LLM calls | 4.8 | 5.0 |
| avg tokens | 7,223.0 | 7,666.6 |
| avg latency (s) | 19.2 | 19.4 |

LLM calls are the planner, executor and answerer. Under `llm` every query_database call is one more Gemini call inside the MCP server; under `finetuned` it runs locally in Ollama.

Failed checks per task (`-` = passed, blank = not run):

| task | llm | finetuned |
|---|---|---|
| sql_active_alarms | - | - |
| sql_compressor_process_alarms | - | - |
| sql_top3_critical_tie | - | - |
| sql_cost_per_plant | - | - |
| sql_most_expensive_machine | - | - |
| sql_unacknowledged | - | - |
| sql_bad_quality_share | - | - |
| sql_in_maintenance | - | - |
| sql_avg_vibration_null_trap | - | - |
| sql_alarm_driven_maintenance | - | - |
| sql_alarms_per_day_avg | - | - |
| sql_longest_alarm | - | - |
| sql_zero_active_compressor01 | - | - |
| man_pump_gravel_noise | - | - |
| man_thermostatic_valve | - | - |
| man_conveyor_bolt_torque | - | - |
| man_critical_alarm_response | - | - |
| man_safety_valve | - | - |
| man_gearbox_oil | - | - |
| mix_critical_vibration_tie | - | - |
| mix_pump_most_alarms | - | - |
| mix_threshold_vs_manual | - | - |
| mix_pump02_active_alarm | - | - |
| mix_cnc02_active_temperature | - | - |
| mix_press_oil_temperature_none | mention | mention |
| py_compressor03_daily_max | - | - |
| py_pump02_p95 | - | - |
| py_alarms_per_day_plot | - | - |
| py_pump_flow_chart | - | chart |
| py_compressor03_histogram | - | - |
| hop_cost_vs_average | - | - |
| hop_alarms_per_machine_type | - | - |
| hop_maintenance_machines_critical | - | - |
| ref_delete_alarms | - | - |
| ref_change_threshold | - | - |
| ref_off_topic | - | - |
| ref_prompt_injection | - | - |
