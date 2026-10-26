# Candidate eval tasks (collected while building)

Failure cases seen during development. Each becomes a task in `eval/tasks.yaml` (Step 10)
and a pattern to cover in the fine-tuning data (Step 6b).

| Seen in | Question | What went wrong | Correct behaviour |
|---|---|---|---|
| Step 3 (gemini-3.1-flash-lite) | "Which 3 machines had the most critical alarms, and how many each?" | `ORDER BY n DESC LIMIT 3` while 5 machines tie at 3 alarms, so the answer silently drops 2 of them | Include ties (`WHERE n >= (3rd value)` or `RANK() <= 3`), or say in the answer that there is a tie |
