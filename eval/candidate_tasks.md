# Candidate eval tasks (collected while building)

Failure cases seen during development. Each becomes a task in `eval/tasks.yaml` (Step 10)
and a pattern to cover in the fine-tuning data (Step 6b).

| Seen in | Question | What went wrong | Correct behaviour |
|---|---|---|---|
| Step 3 (gemini-3.1-flash-lite) | "Which 3 machines had the most critical alarms, and how many each?" | `ORDER BY n DESC LIMIT 3` while 5 machines tie at 3 alarms, so the answer silently drops 2 of them | Include ties (`WHERE n >= (3rd value)` or `RANK() <= 3`), or say in the answer that there is a tie |
| Step 4 (search_manuals) | "What must the operator do when a critical alarm is raised?" | Retrieval returns the threshold tables ("HIGH_HIGH is critical"), not section 3 "Alarm response" | The agent should search again with different wording (e.g. "HIGH_HIGH alarm response procedure"), so this is a good multi-step task |
| Step 5 (sql + run_python) | "Plot the discharge temperature of Compressor-03 and show when it was in alarm" | Daily **average** stays at about 88 °C and never comes near the 105 °C threshold, although the machine had 9 alarms; averaging hides short excursions | Use the daily max (or the raw readings around each alarm) and mark the alarm periods |
| Step 6 (agent, demo Q4) | "Which machine had the most critical vibration alarms, and what does its manual recommend?" | 4 machines tie at 1 critical vibration alarm; SQL used `LIMIT 1` without a count, so the agent presented Hydraulic-Press-02 as the only answer | Report the tie (Pump-06, Hydraulic-Press-01/02, Conveyor-02, 1 each) and give manual advice per machine type, or ask which one |
| Step 6 (agent, demo Q3) | "Plot the daily maximum discharge temperature of Compressor-03…" | SQL silently filtered `quality = 'good'`, so the reported max is 95.1-95.8 °C depending on the filter; the true max including 'uncertain' readings is 95.75 °C | State the quality filter, or include 'uncertain' readings by default |
