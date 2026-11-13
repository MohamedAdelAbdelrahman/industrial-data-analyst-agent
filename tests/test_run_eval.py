"""run_eval: the task file is valid and the checkers score saved runs the way the task file says."""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "eval"))
import run_eval
from run_eval import check, has_number, has_text, load_tasks, numbers_in, sql_hit

DB = ROOT / "data" / "sensors.db"
TASKS = {t["id"]: t for t in load_tasks()}


def run(answer: str = "", status: str = "answered", tool_log: list | None = None) -> dict:
    return {"status": status, "final_answer": answer, "tool_log": tool_log or []}


def failed(task_id: str, result: dict) -> dict:
    return {k: v for k, v in check(TASKS[task_id], result, DB).items() if v}


def test_task_file_is_valid():
    assert 30 <= len(TASKS) <= 40
    assert {t["category"] for t in TASKS.values()} == set(run_eval.CATEGORIES)


@pytest.mark.parametrize("task", [t for t in TASKS.values() if "expect_sql" in t], ids=lambda t: t["id"])
def test_reference_sql_runs_and_is_small(task):
    cells = run_eval.reference_cells(DB, task["expect_sql"])
    assert 0 < len(cells) <= 12


def test_numbers_skip_names_and_dates():
    assert numbers_in("Pump-03 had 12 alarms on 2026-08-12, costing €13,721.97 at 0.5 bar") == [12, 2026, 13721.97, 0.5]


def test_has_number_tolerance():
    assert has_number("about 2.00 mm/s", 2.004737)            # rounded
    assert has_number("€4,724", 4724.43)
    assert not has_number("5 alarms", 6)
    assert not has_number("Pump-05", 5)


def test_has_text_regex_and_case():
    assert has_text("Use iso vg 220 oil", "ISO VG 220")
    zero = r"re:(?<![\d.,-])0(?![\d.,])"
    assert has_text("Compressor-01 has 0 active alarms", zero)
    assert not has_text("Compressor-01 has 2 active alarms at 0.5 bar", zero)


def test_tie_needs_every_tied_machine():
    five = "Conveyor-01, Hydraulic-Press-01, Pump-01, Pump-02 and Pump-03 tie with 3 critical alarms each."
    assert failed("sql_top3_critical_tie", run(five)) == {}
    three = "Pump-01, Pump-02 and Pump-03 had 3 critical alarms each."
    assert "expect_sql" in failed("sql_top3_critical_tie", run(three))


def test_alternatives_in_numbers_and_mention():
    assert failed("sql_longest_alarm", run("CNC-Mill-01's current alarm lasted 585 minutes.")) == {}
    assert failed("sql_longest_alarm", run("CNC-Mill-01 current: 9.75 h")) == {}


def test_refusal_tasks():
    assert failed("ref_delete_alarms", run("I can't change data.", status="refused")) == {}
    assert "refuse" in failed("ref_delete_alarms", run("Deleted.", status="answered"))
    assert "not_refused" in failed("sql_in_maintenance", run("No.", status="refused"))


def test_manual_and_chart_come_from_the_tool_log():
    hit = {"name": "search_manuals", "output": {"hits": [{"machine_type": "Pump", "section": "4.3 High vibration"}]}}
    other = {"name": "search_manuals", "output": {"hits": [{"machine_type": "Pump", "section": "2. Sensors"}]}}
    answer = "A gravel-like noise means cavitation: keep suction pressure above 0.5 bar."
    assert failed("man_pump_gravel_noise", run(answer, tool_log=[hit])) == {}
    assert "manual" in failed("man_pump_gravel_noise", run(answer, tool_log=[other]))
    chart = {"name": "run_python", "output": {"images": [{"name": "flow.png"}]}}
    assert "chart" in failed("py_pump_flow_chart", run("Pump-01 and Pump-02 plotted."))
    assert failed("py_pump_flow_chart", run("Pump-01 and Pump-02 plotted.", tool_log=[chart])) == {}


def test_sql_hit_looks_at_query_rows():
    task = TASKS["sql_most_expensive_machine"]
    right = {"name": "query_database", "output": {"rows": [["Pump-01", 13721.97]], "error": None}}
    wrong = {"name": "query_database", "output": {"rows": [["CNC-Mill-01", 12744.01]], "error": None}}
    assert sql_hit(task, run(tool_log=[wrong, right]), DB) is True
    assert sql_hit(task, run(tool_log=[wrong]), DB) is False
    assert sql_hit(TASKS["man_thermostatic_valve"], run(), DB) is None


def test_saved_keeps_the_latest_line(tmp_path, monkeypatch):
    monkeypatch.setattr(run_eval, "RESULTS", tmp_path)
    p = run_eval.results_path("r", "llm")
    p.parent.mkdir(parents=True)
    p.write_text('{"id": "a", "v": 1}\n{"id": "a", "v": 2}\n', encoding="utf-8")
    assert run_eval.saved("r", "llm")["a"]["v"] == 2


def test_pool_wait_needs_every_model_of_a_pool_blocked(tmp_path, monkeypatch):
    from datetime import datetime, timedelta

    from ida.config import get_settings
    from ida.model_pool import QuotaMemory
    monkeypatch.setenv("QUOTA_PATH", str(tmp_path / "quota.json"))
    memory, soon = QuotaMemory(tmp_path / "quota.json"), datetime.now().astimezone() + timedelta(seconds=90)
    planners = get_settings().planner_models
    memory.block(planners[0], soon, "rate limited")
    assert run_eval.pool_wait_s("finetuned") == 0           # the next planner model is free
    for m in planners:
        memory.block(m, soon, "rate limited")
    assert 80 < run_eval.pool_wait_s("finetuned") <= 90


def test_single_agent_waits_only_on_its_pools(tmp_path, monkeypatch):
    from datetime import datetime, timedelta

    from ida.config import get_settings
    from ida.model_pool import QuotaMemory
    monkeypatch.setenv("QUOTA_PATH", str(tmp_path / "quota.json"))
    memory, soon = QuotaMemory(tmp_path / "quota.json"), datetime.now().astimezone() + timedelta(seconds=90)
    for m in get_settings().executor_models:
        memory.block(m, soon, "rate limited")
    assert run_eval.pool_wait_s("finetuned", "single") == 0      # the single agent has no executor
    assert run_eval.pool_wait_s("finetuned", "planner") > 0


def test_compare_lists_the_tasks_that_differ(tmp_path, monkeypatch):
    import json
    monkeypatch.setattr(run_eval, "RESULTS", tmp_path)
    usage = {"m": {"input": 1, "output": 1, "calls": 2}}
    for name, arch, status in (("a", "planner", "answered"), ("b", "single", "refused")):
        p = run_eval.results_path(name, "llm")
        p.parent.mkdir(parents=True)
        row = {"id": "ref_off_topic", "category": "should_refuse", "arch": arch, "status": status,
               "final_answer": "", "tool_log": [], "tool_calls": 0, "llm_calls": 2, "tokens": 2,
               "latency_s": 1.0, "usage": usage}
        p.write_text(json.dumps(row) + "\n", encoding="utf-8")
    md = run_eval.compare("a", "b", [TASKS["ref_off_topic"]])
    assert "| `a` (planner) | `b` (single) |" in md and "SQL_BACKEND=finetuned" not in md
    assert "| **passed** | 0/1 (0%) | 1/1 (100%) |" in md
    assert "- `ref_off_topic`: a refuse; b -" in md
    assert (tmp_path / "compare_a_vs_b.md").exists()
