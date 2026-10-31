"""Training-data pipeline for the fine-tuned Text-to-SQL model, with a fake model (no API calls)."""
import json
import random
from types import SimpleNamespace

import pytest
from langchain_core.runnables import RunnableLambda

from ida.mcp_server.db import run_query
from ida.mcp_server.sql_generator import build_prompt
from ida.model_pool import ModelPool, QuotaMemory
from ida.training_data import (
    CATEGORIES,
    OFF_CATEGORY,
    actual_category,
    batch_prompt,
    check_pair,
    db_values,
    generate,
    in_test_split,
    mixes_units,
    parse_batch,
    query_fingerprint,
    question_key,
    read_jsonl,
    recheck,
    same_value,
    sql_skeleton,
    thinnest_category,
    write_splits,
)

GOOD_SQL = "SELECT name FROM machines WHERE status = 'running'"


def fake_pool(tmp_path, replies):
    """Answers with the given batches in turn, like a chat model would."""
    answers = iter(replies)
    model = RunnableLambda(lambda _: SimpleNamespace(
        text=json.dumps(next(answers)), response_metadata={"model_name": "fake-model"}))
    return ModelPool("datagen", [("fake-model", model)], QuotaMemory(tmp_path / "quota.json"))


@pytest.mark.parametrize("sql", [
    GOOD_SQL,
    f"  {GOOD_SQL}\n  ORDER BY name;  ",
    "SELECT COUNT(*) FROM machines",
    "WITH m AS (SELECT name FROM machines) SELECT name FROM m",
])
def test_check_pair_keeps_good_sql(small_db, sql):
    cleaned, reason = check_pair(small_db, "Which machines?", sql, 5)
    assert reason is None
    assert "\n" not in cleaned and not cleaned.endswith(";")


@pytest.mark.parametrize("sql, reason", [
    ("SELECT nope FROM machines", "SQL error"),
    ("SELECT * FROM users", "failed the validator"),
    ("DELETE FROM alarms", "failed the validator"),
    ("SELECT name FROM machines WHERE status = 'on fire'", "empty result"),
    ("SELECT COUNT(*) FROM machines WHERE status = 'on fire'", "empty result"),
    ("SELECT AVG(value) FROM readings WHERE quality = 'on fire'", "empty result"),
    ("SELECT name FROM machines WHERE install_date < date('now')", "uses the real clock"),
    ("SELECT name FROM machines WHERE install_date < CURRENT_TIMESTAMP", "uses the real clock"),
    ("SELECT name FROM machines -- all of them", "has a comment"),
    ('SELECT "nope" FROM machines', "double quotes"),
])
def test_check_pair_rejects(small_db, sql, reason):
    assert check_pair(small_db, "Which machines?", sql, 5) == (None, reason)


GOOD_ONLY = "SELECT AVG(value) FROM readings WHERE sensor_id = 1 AND quality = 'good'"


def test_quality_filter_needs_a_question_that_asks_for_it(small_db):
    assert check_pair(small_db, "Average reading?", GOOD_ONLY, 5) == (None, "unasked quality filter")
    assert check_pair(small_db, "Average over good readings?", GOOD_ONLY, 5) == (GOOD_ONLY, None)
    in_list = "SELECT AVG(value) FROM readings WHERE sensor_id = 1 AND quality IN ('good', 'uncertain')"
    assert check_pair(small_db, "Average reading?", in_list, 5) == (None, "unasked quality filter")
    unrelated = "SELECT name FROM machines WHERE status = 'running'"
    assert check_pair(small_db, "Average reading?", unrelated, 5) == (unrelated, None)


# Most machines share a plant, so sorting on plant ties; machine names are unique.
RANKED = "WITH r AS (SELECT name, ROW_NUMBER() OVER (ORDER BY {key}) AS rn FROM machines) SELECT name FROM r "


@pytest.mark.parametrize("sql", [
    "SELECT name FROM machines ORDER BY plant LIMIT 1",
    "SELECT name, plant AS p FROM machines ORDER BY p LIMIT 1",
    "SELECT name, plant FROM machines ORDER BY 2 LIMIT 1",
    "SELECT name FROM machines ORDER BY plant, name LIMIT 1",                # a tiebreaker just hides it
    "SELECT name FROM machines ORDER BY plant LIMIT 1 OFFSET 2",
    "WITH t AS (SELECT name FROM machines ORDER BY plant LIMIT 2) SELECT name FROM t",
    RANKED.format(key="plant") + "WHERE rn = 1",
    RANKED.format(key="plant, name") + "WHERE rn <= 2",
])
def test_check_pair_rejects_a_cut_through_a_tie(small_db, sql):
    assert check_pair(small_db, "Which machine?", sql, 5) == (None, "hides a tie")


@pytest.mark.parametrize("sql", [
    "SELECT name FROM machines ORDER BY name LIMIT 1",
    "SELECT name FROM machines ORDER BY name DESC LIMIT 3 OFFSET 1",
    "SELECT name FROM machines ORDER BY plant LIMIT 1000",                   # nothing is cut off
    "WITH t AS (SELECT name FROM machines ORDER BY name LIMIT 2) SELECT name FROM t",
    RANKED.format(key="name") + "WHERE rn = 1",
    RANKED.format(key="plant").strip(),                                      # numbered, nothing dropped
    "SELECT name FROM (SELECT name, RANK() OVER (ORDER BY plant) AS r FROM machines) WHERE r = 1",
    "SELECT name FROM machines WHERE install_date = (SELECT MAX(install_date) FROM machines)",
])
def test_check_pair_keeps_rankings_without_a_hidden_tie(small_db, sql):
    assert check_pair(small_db, "Which machine?", sql, 5) == (sql, None)


def test_check_pair_rejects_limit_without_order(small_db):
    sql = "SELECT name FROM machines LIMIT 1"
    assert check_pair(small_db, "Which machine?", sql, 5) == (None, "LIMIT without ORDER BY")


def test_check_pair_rejects_a_bare_column_beside_max(small_db):
    bare = "SELECT name, MAX(install_date) FROM machines"
    assert check_pair(small_db, "Newest machine?", bare, 5) == (None, "bare column beside MIN/MAX")
    grouped = "SELECT plant, MAX(install_date) FROM machines GROUP BY plant"
    assert check_pair(small_db, "Newest per plant?", grouped, 5) == (grouped, None)


def test_ties_survive_julianday_rounding():
    assert same_value(8.00000000372529, 7.99999999627471)
    assert same_value("Pump-01", "Pump-01") and same_value(None, None)
    assert not same_value(8.0, 8.5) and not same_value("Pump-01", "Pump-02") and not same_value(None, 0)


READINGS = "readings r JOIN sensors s ON r.sensor_id = s.sensor_id"


@pytest.mark.parametrize("sql, mixed", [
    (f"SELECT MAX(r.value) FROM {READINGS}", True),
    ("SELECT MAX(peak_value) FROM alarms WHERE alarm_type = 'HIGH'", True),
    ("SELECT name FROM alarms a JOIN machines m ON a.machine_id = m.machine_id ORDER BY a.peak_value DESC", True),
    (f"SELECT AVG(s.warn_threshold - r.value) FROM {READINGS}", True),       # a difference keeps the unit
    (f"SELECT AVG(r.value) FROM {READINGS} WHERE s.sensor_type = 'rpm'", False),
    (f"SELECT s.sensor_type, MAX(r.value) FROM {READINGS} GROUP BY s.sensor_type", False),
    (f"SELECT AVG(r.value) FROM {READINGS} WHERE s.mount_location = 'outlet'", False),
    (f"SELECT MAX(r.value / s.crit_threshold) FROM {READINGS}", False),      # a ratio has no unit
    (f"SELECT SUM(CASE WHEN r.value > s.warn_threshold THEN 1 ELSE 0 END) FROM {READINGS}", False),
    ("SELECT COUNT(*) FROM readings", False),
])
def test_mixes_units(sql, mixed):
    assert mixes_units(sql) is mixed


def test_recheck_moves_failures_to_rejected(tmp_path, small_db):
    raw, rejected = tmp_path / "raw.jsonl", tmp_path / "rejected.jsonl"
    pairs = [{"question": "Which machines run?", "sql": GOOD_SQL, "category": "joins", "model": "m"},
             {"question": "First plant?", "sql": "SELECT name FROM machines ORDER BY plant LIMIT 1",
              "category": "ranking_cte", "model": "m"}]
    raw.write_text("".join(json.dumps(p) + "\n" for p in pairs), encoding="utf-8")

    assert recheck(small_db, raw, rejected, 5) == {"kept": 1, "hides a tie": 1}
    assert read_jsonl(raw) == pairs[:1]
    assert read_jsonl(rejected) == [{**pairs[1], "reason": "hides a tie"}]


def test_parse_batch_handles_fences_and_junk():
    pair = {"question": "How many?", "sql": "SELECT 1"}
    assert parse_batch(f"Sure!\n```json\n{json.dumps([pair])}\n```") == [pair]
    assert parse_batch(json.dumps([pair, {"question": "no sql"}, "text"])) == [pair]
    assert parse_batch("I cannot do that.") == []
    assert parse_batch('[{"question": "cut off') == []


def test_question_key_ignores_case_and_punctuation():
    assert question_key("How many  pumps?") == question_key("how many pumps")


def test_skeleton_ignores_what_a_paraphrase_changes():
    base = "SELECT COUNT(*) FROM machines WHERE plant = 'Plant A'"
    assert sql_skeleton(base) == sql_skeleton("SELECT COUNT(*) AS n FROM machines m WHERE m.plant = 'Plant B'")
    assert sql_skeleton(base) != sql_skeleton("SELECT COUNT(*) FROM machines WHERE line = 'Line 1'")


def test_paraphrased_queries_land_on_the_same_side():
    sql = [f"SELECT COUNT(*) AS n FROM alarms a WHERE a.machine_id = {i}" for i in range(50)]
    assert len({in_test_split(s) for s in sql}) == 1


def test_batch_prompt_lists_values_and_covered_questions(small_db):
    values = db_values(small_db)
    assert "Compressor-03" in values and "HIGH_HIGH" in values
    prompt = batch_prompt("CREATE TABLE x", values, "joins", "short", "pumps", ["Old question?"])
    assert "Old question?" in prompt and CATEGORIES["joins"] in prompt and "2026-10-01 00:00:00" in prompt


def test_thinnest_category_balances_the_data():
    rows = [{"category": c} for c in CATEGORIES if c != "durations"]
    assert thinnest_category(rows, random.Random(0)) == "durations"


def test_off_topic_pairs_do_not_count_for_their_category():
    assert actual_category("reading_quality", "SELECT COUNT(*) FROM readings WHERE quality = 'bad'") == "reading_quality"
    assert actual_category("reading_quality", "SELECT SUM(cost_eur) FROM maintenance_events") == OFF_CATEGORY
    assert actual_category("active_alarms", "SELECT COUNT(*) FROM alarms WHERE cleared_at IS NULL") == "active_alarms"
    assert actual_category("joins", "SELECT 1") == "joins"                # no SQL pattern for this category
    rows = [{"category": c} for c in CATEGORIES] + [{"category": OFF_CATEGORY}] * 5
    rows.remove({"category": "durations"})
    assert thinnest_category(rows, random.Random(0)) == "durations"


def test_generate_keeps_good_pairs_and_resumes(tmp_path, small_db):
    raw, rejected = tmp_path / "raw.jsonl", tmp_path / "rejected.jsonl"
    first = [{"question": "Which machines run?", "sql": GOOD_SQL},
             {"question": "Broken", "sql": "SELECT nope FROM machines"}]
    second = [{"question": "which machines RUN", "sql": GOOD_SQL},           # same question as batch 1
              {"question": "How many machines?", "sql": "SELECT COUNT(*) FROM machines"}]

    kwargs = dict(raw_path=raw, rejected_path=rejected, max_requests=1, pause_s=0, rng=random.Random(0))
    outcome = generate(fake_pool(tmp_path, [first]), small_db, "schema", **kwargs)
    assert outcome == {"kept": 1, "SQL error": 1}

    outcome = generate(fake_pool(tmp_path, [second]), small_db, "schema", **kwargs)      # a later run
    assert outcome == {"kept": 1, "duplicate question": 1}

    rows = read_jsonl(raw)
    assert [r["question"] for r in rows] == ["Which machines run?", "How many machines?"]
    assert rows[0]["model"] == "fake-model" and rows[0]["category"] in {*CATEGORIES, OFF_CATEGORY}
    assert sorted(r["reason"] for r in read_jsonl(rejected)) == ["SQL error", "duplicate question"]


def test_generate_stops_at_target(tmp_path, small_db):
    pool = fake_pool(tmp_path, [[{"question": "Which machines run?", "sql": GOOD_SQL}]])     # one reply only
    outcome = generate(pool, small_db, "schema", raw_path=tmp_path / "raw.jsonl",
                       rejected_path=tmp_path / "rejected.jsonl", target=1, max_requests=5, pause_s=0)
    assert outcome == {"kept": 1}


def write_raw(path, sql_list):
    pairs = [{"question": f"Q{i}", "sql": sql, "category": "joins", "model": "m"} for i, sql in enumerate(sql_list)]
    path.write_text("".join(json.dumps(p) + "\n" for p in pairs), encoding="utf-8")


def test_splits_use_the_inference_prompt_and_do_not_overlap(tmp_path, small_db):
    raw = tmp_path / "raw.jsonl"
    write_raw(raw, ["SELECT " + " + ".join(["machine_id"] * (i + 1)) + " FROM machines" for i in range(200)])

    n_train, n_test = write_splits("SCHEMA", small_db, raw, tmp_path)
    train, test = read_jsonl(tmp_path / "train.jsonl"), read_jsonl(tmp_path / "test.jsonl")
    assert (n_train, n_test) == (len(train), len(test))
    assert n_train + n_test == 200 and 5 <= n_test <= 40
    assert not {r["question"] for r in train} & {r["question"] for r in test}
    assert train[0]["prompt"] == build_prompt("SCHEMA", train[0]["question"])
    assert train[0]["completion"] == train[0]["sql"]


JOIN_A = ("SELECT a.alarm_id FROM alarms a JOIN machines m ON a.machine_id = m.machine_id "
          "WHERE m.plant = 'Plant A' AND a.severity = 'critical'")
JOIN_B = ("SELECT a.alarm_id FROM machines m JOIN alarms a ON m.machine_id = a.machine_id "
          "WHERE a.severity = 'critical' AND m.plant = 'Plant A'")


def test_fingerprint_sees_through_join_order_but_not_other_queries(small_db):
    assert sql_skeleton(JOIN_A) != sql_skeleton(JOIN_B)
    assert query_fingerprint(small_db, JOIN_A) == query_fingerprint(small_db, JOIN_B)
    same_answer = "SELECT 'Plant B'", "SELECT plant FROM machines WHERE machine_id = 1"       # same rows only
    assert run_query(small_db, same_answer[1], 1, 5).rows == [("Plant B",)]
    assert query_fingerprint(small_db, same_answer[0]) != query_fingerprint(small_db, same_answer[1])


def test_splits_drop_a_test_pair_that_repeats_a_training_query(tmp_path, small_db, monkeypatch):
    raw = tmp_path / "raw.jsonl"
    other = "SELECT name FROM machines WHERE status = 'running'"
    write_raw(raw, [JOIN_A, JOIN_B, other])
    monkeypatch.setattr("ida.training_data.in_test_split", lambda sql: sql in (JOIN_B, other))

    assert write_splits("SCHEMA", small_db, raw, tmp_path) == (1, 1)
    assert [r["sql"] for r in read_jsonl(tmp_path / "test.jsonl")] == [other]
