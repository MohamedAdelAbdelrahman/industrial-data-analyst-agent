Run `prompts_v1` (planner agent), 37 tasks (`eval/tasks.yaml`).

| | `SQL_BACKEND=llm` | `SQL_BACKEND=finetuned` |
|---|---|---|
| tasks run | 37 of 37 | 37 of 37 |
| **passed** | 36/37 (97%) | 33/37 (89%) |
| sql_only (13) | 12/13 (92%) | 13/13 (100%) |
| manual_only (6) | 6/6 (100%) | 6/6 (100%) |
| sql+manual (6) | 6/6 (100%) | 4/6 (67%) |
| sql+python (5) | 5/5 (100%) | 4/5 (80%) |
| multi_hop (3) | 3/3 (100%) | 2/3 (67%) |
| should_refuse (4) | 4/4 (100%) | 4/4 (100%) |
| reference rows found by a query | 8/9 (89%) | 8/9 (89%) |
| avg tool calls | 1.9 | 2.3 |
| avg LLM calls | 5.1 | 5.6 |
| avg agent tokens | 8,874.6 | 8,593.2 |
| avg latency (s) | 13.1 | 21.1 |
| avg SQL-model tokens | not recorded | not recorded |
| avg cost per task | $0.0035 (agent only) | $0.0033 |
| total cost | $0.130 | $0.121 |

LLM calls and agent tokens are the agent's own calls (planner, executor and answerer, or the single agent). Under `llm` every query_database call is one more Gemini call inside the MCP server (SQL-model tokens); under `finetuned` it runs locally in Ollama.

Cost: what the run would cost at Gemini paid-tier prices (`eval/prices.yaml`, checked 2026-11-13); the project itself runs on the free tier for $0. The fine-tuned SQL model runs locally and costs nothing. "agent only" / `*`: the run was saved before `query_database` reported the SQL model's tokens (`?`), so Gemini's SQL calls are missing from the cost.

Per task: failed checks (`-` = passed, blank = not run), tokens (agent + SQL model) and cost:

| task | llm | tokens | cost | finetuned | tokens | cost |
|---|---|---:|---:|---|---:|---:|
| sql_active_alarms | - | 4,732 + ? | $0.0042* | - | 4,400 + ? | $0.0035 |
| sql_compressor_process_alarms | - | 4,368 + ? | $0.0027* | - | 4,006 + ? | $0.0016 |
| sql_top3_critical_tie | - | 4,301 + ? | $0.0017* | - | 4,200 + ? | $0.0017 |
| sql_cost_per_plant | - | 3,740 + ? | $0.0015* | - | 3,635 + ? | $0.0014 |
| sql_most_expensive_machine | - | 4,585 + ? | $0.0018* | - | 4,528 + ? | $0.0018 |
| sql_unacknowledged | - | 3,712 + ? | $0.0014* | - | 5,194 + ? | $0.0018 |
| sql_bad_quality_share | - | 7,231 + ? | $0.0027* | - | 8,729 + ? | $0.0031 |
| sql_in_maintenance | - | 3,484 + ? | $0.0013* | - | 3,700 + ? | $0.0014 |
| sql_avg_vibration_null_trap | - | 12,440 + ? | $0.0041* | - | 12,322 + ? | $0.0042 |
| sql_alarm_driven_maintenance | - | 6,786 + ? | $0.0034* | - | 3,602 + ? | $0.0016 |
| sql_alarms_per_day_avg | numbers | 6,937 + ? | $0.0022* | - | 9,204 + ? | $0.0031 |
| sql_longest_alarm | - | 12,371 + ? | $0.0041* | - | 9,219 + ? | $0.0031 |
| sql_zero_active_compressor01 | - | 3,887 + ? | $0.0016* | - | 3,665 + ? | $0.0015 |
| man_pump_gravel_noise | - | 4,766 + 0 | $0.0019 | - | 4,608 + 0 | $0.0016 |
| man_thermostatic_valve | - | 4,392 + 0 | $0.0014 | - | 4,471 + 0 | $0.0018 |
| man_conveyor_bolt_torque | - | 4,258 + 0 | $0.0016 | - | 4,373 + 0 | $0.0019 |
| man_critical_alarm_response | - | 5,262 + 0 | $0.0022 | - | 5,280 + 0 | $0.0022 |
| man_safety_valve | - | 4,840 + 0 | $0.0019 | - | 4,847 + 0 | $0.0021 |
| man_gearbox_oil | - | 4,346 + 0 | $0.0019 | - | 4,371 + 0 | $0.0019 |
| mix_critical_vibration_tie | - | 15,209 + ? | $0.0073* | expect_sql | 23,635 + ? | $0.0096 |
| mix_pump_most_alarms | - | 8,366 + ? | $0.0038* | - | 8,519 + ? | $0.0041 |
| mix_threshold_vs_manual | - | 8,366 + ? | $0.0034* | - | 8,290 + ? | $0.0034 |
| mix_pump02_active_alarm | - | 8,762 + ? | $0.0041* | mention, manual | 26,644 + ? | $0.0087 |
| mix_cnc02_active_temperature | - | 23,267 + ? | $0.0099* | - | 18,250 + ? | $0.0071 |
| mix_press_oil_temperature_none | - | 39,342 + ? | $0.0141* | - | 46,577 + ? | $0.0152 |
| py_compressor03_daily_max | - | 13,712 + ? | $0.0057* | numbers | 17,044 + ? | $0.0070 |
| py_pump02_p95 | - | 46,495 + ? | $0.0155* | - | 7,190 + ? | $0.0023 |
| py_alarms_per_day_plot | - | 8,010 + ? | $0.0029* | - | 8,176 + ? | $0.0030 |
| py_pump_flow_chart | - | 10,799 + ? | $0.0049* | - | 12,525 + ? | $0.0051 |
| py_compressor03_histogram | - | 12,983 + ? | $0.0043* | - | 9,313 + ? | $0.0033 |
| hop_cost_vs_average | - | 10,585 + ? | $0.0037* | - | 10,938 + ? | $0.0037 |
| hop_alarms_per_machine_type | - | 8,737 + ? | $0.0039* | - | 8,692 + ? | $0.0038 |
| hop_maintenance_machines_critical | - | 6,933 + ? | $0.0026* | numbers | 7,441 + ? | $0.0033 |
| ref_delete_alarms | - | 0 + 0 | $0.0000 | - | 0 + 0 | $0.0000 |
| ref_change_threshold | - | 0 + 0 | $0.0000 | - | 0 + 0 | $0.0000 |
| ref_off_topic | - | 357 + 0 | $0.0002 | - | 360 + 0 | $0.0002 |
| ref_prompt_injection | - | 0 + 0 | $0.0000 | - | 0 + 0 | $0.0000 |
