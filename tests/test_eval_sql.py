"""eval_sql: what counts as a right answer on the Text-to-SQL test split."""
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from langchain_core.runnables import RunnableLambda

from ida.model_pool import AllModelsUnavailable, ModelPool, QuotaMemory
from ida.training_data import BENCHMARK_MODEL

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "eval"))
import eval_sql
from eval_sql import ask, evaluate, score

GOLD = "SELECT machine_type, COUNT(*) FROM machines GROUP BY machine_type"


def overloaded_once():
    calls = []

    def answer(_):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("503 UNAVAILABLE")
        return "SELECT 1"
    return RunnableLambda(answer)


def test_ask_waits_out_a_short_block(tmp_path, monkeypatch):
    memory = QuotaMemory(tmp_path / "quota.json")
    pool = ModelPool("sql", [(BENCHMARK_MODEL, overloaded_once())], memory)
    monkeypatch.setattr(eval_sql.time, "sleep", lambda _: memory.reset())
    assert ask(pool, "q") == "SELECT 1"


def test_ask_stops_on_a_long_block(tmp_path):
    memory = QuotaMemory(tmp_path / "quota.json")
    memory.block(BENCHMARK_MODEL, datetime.now(UTC) + timedelta(hours=5), "daily quota exhausted")
    with pytest.raises(AllModelsUnavailable):
        ask(ModelPool("sql", [(BENCHMARK_MODEL, overloaded_once())], memory), "q")


@pytest.mark.parametrize("answer, outcome", [
    (GOLD, "right"),
    (f"```sql\n{GOLD};\n```", "right"),
    ("SELECT COUNT(*), machine_type FROM machines GROUP BY machine_type", "right"),
    ("SELECT machine_type, COUNT(*) FROM machines GROUP BY machine_type ORDER BY 2 DESC, 1", "right"),
    ("SELECT machine_type, COUNT(*), 1 FROM machines GROUP BY machine_type", "wrong rows"),
    ("SELECT machine_type FROM machines GROUP BY machine_type", "wrong rows"),
    ("SELECT nope FROM machines", "failed"),
    ("DELETE FROM machines", "failed"),
    ("I cannot answer that.", "failed"),
])
def test_score(small_db, answer, outcome):
    assert score(small_db, GOLD, answer) == outcome


def test_evaluate_leaves_a_missing_gemini_answer_unscored(small_db):
    row = {"question": "q", "category": "c", "gold": GOLD, "base": GOLD, "finetuned": "SELECT nope FROM machines"}
    outcome = evaluate(small_db, [row], {})[0]["outcome"]
    assert outcome == {"base": "right", "finetuned": "failed", "gemini": None}
