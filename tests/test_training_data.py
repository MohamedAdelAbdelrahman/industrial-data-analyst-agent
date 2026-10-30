"""Training-data pipeline for the fine-tuned Text-to-SQL model, with a fake model (no API calls)."""
import json
import random
from types import SimpleNamespace

import pytest
from langchain_core.runnables import RunnableLambda

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
    parse_batch,
    question_key,
    read_jsonl,
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


GOOD_ONLY = "SELECT AVG(value) FROM readings WHERE quality = 'good'"


def test_quality_filter_needs_a_question_that_asks_for_it(small_db):
    assert check_pair(small_db, "Average reading?", GOOD_ONLY, 5) == (None, "unasked quality filter")
    assert check_pair(small_db, "Average over good readings?", GOOD_ONLY, 5) == (GOOD_ONLY, None)
    in_list = "SELECT AVG(value) FROM readings WHERE quality IN ('good', 'uncertain')"
    assert check_pair(small_db, "Average reading?", in_list, 5) == (None, "unasked quality filter")
    unrelated = "SELECT name FROM machines WHERE status = 'running'"
    assert check_pair(small_db, "Average reading?", unrelated, 5) == (unrelated, None)


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


def test_splits_use_the_inference_prompt_and_do_not_overlap(tmp_path):
    raw = tmp_path / "raw.jsonl"
    pairs = [{"question": f"Q{i}", "sql": f"SELECT col{i} FROM machines", "category": "joins", "model": "m"}
             for i in range(200)]
    raw.write_text("".join(json.dumps(p) + "\n" for p in pairs), encoding="utf-8")

    n_train, n_test = write_splits("SCHEMA", raw, tmp_path)
    train, test = read_jsonl(tmp_path / "train.jsonl"), read_jsonl(tmp_path / "test.jsonl")
    assert (n_train, n_test) == (len(train), len(test))
    assert n_train + n_test == 200 and 5 <= n_test <= 40
    assert not {r["question"] for r in train} & {r["question"] for r in test}
    assert train[0]["prompt"] == build_prompt("SCHEMA", train[0]["question"])
    assert train[0]["completion"] == train[0]["sql"]
