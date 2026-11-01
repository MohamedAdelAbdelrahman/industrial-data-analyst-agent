"""Execution accuracy on the held-out test split: base Qwen, fine-tuned Qwen and Gemini (Step 6b).

An answer is right when it returns the same rows as the gold SQL. Row order and column order do not count, and an
extra or missing column is wrong. Gemini's score is an upper estimate: Gemini models wrote the gold SQL.

Usage:
    python eval/eval_sql.py --ask-gemini     # asks for the missing Gemini answers (kept, so it can resume), then scores
    python eval/eval_sql.py                  # scores the answers already there
"""
import argparse
import sqlite3
import sys
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from ida.config import get_settings
from ida.mcp_server.db import QueryTimeoutError, run_query
from ida.mcp_server.sql_generator import extract_sql
from ida.mcp_server.sql_validator import SQLValidationError, validate_sql
from ida.model_pool import AllModelsUnavailable, ModelPool, model_pool
from ida.training_data import BENCHMARK_MODEL, DATA_DIR, PAUSE_S, append_jsonl, read_jsonl, result_cells

MODEL_DIR = DATA_DIR.parent / "model"
PREDICTIONS = MODEL_DIR / "predictions.jsonl"
GEMINI_ANSWERS = MODEL_DIR / "gemini.jsonl"
MAX_ROWS = 5000
TIMEOUT_S = 30.0
MAX_WAIT_S = 1800
BACKENDS = ("base", "finetuned", "gemini")


def score(db_path: Path, gold: str, answer: str) -> str:
    """'right', 'wrong rows' or 'failed' (not valid SQL, SQL error, timeout)."""
    expected = run_query(db_path, validate_sql(gold), MAX_ROWS, TIMEOUT_S).rows
    try:
        rows = run_query(db_path, validate_sql(extract_sql(answer)), MAX_ROWS, TIMEOUT_S).rows
    except (SQLValidationError, QueryTimeoutError, sqlite3.Error):
        return "failed"
    return "right" if result_cells(rows) == result_cells(expected) else "wrong rows"


def ask(pool: ModelPool, prompt: str):
    """Wait out short blocks (the free tier often answers 503). A long one, like a daily limit, stops the run."""
    while True:
        until = pool.memory.blocked_until(BENCHMARK_MODEL)
        wait = (until - datetime.now(UTC)).total_seconds() if until else 0
        if wait > MAX_WAIT_S:
            raise AllModelsUnavailable(f"{BENCHMARK_MODEL} is blocked until {until.astimezone():%Y-%m-%d %H:%M}")
        if wait:
            print(f"blocked, waiting {wait:.0f} s", flush=True)
            time.sleep(wait + 1)
        try:
            return pool.invoke(prompt)
        except Exception:        # the pool blocks the model on errors worth waiting for
            if not pool.memory.blocked_until(BENCHMARK_MODEL):
                raise


def ask_gemini(tests: list[dict], path: Path = GEMINI_ANSWERS) -> None:
    done = {r["question"] for r in read_jsonl(path)} if path.exists() else set()
    pool = model_pool("sql", (BENCHMARK_MODEL,))
    todo = [t for t in tests if t["question"] not in done]
    print(f"{len(done)} answers kept, {len(todo)} to ask")
    for i, test in enumerate(todo, 1):
        try:
            msg = ask(pool, test["prompt"])
        except AllModelsUnavailable as e:
            sys.exit(f"stopped after {i - 1} of {len(todo)}, run again later: {e}")
        append_jsonl(path, [{"question": test["question"], "answer": msg.text,
                             "model": msg.response_metadata.get("model_name", BENCHMARK_MODEL)}])
        print(f"{i}/{len(todo)}", flush=True)
        time.sleep(PAUSE_S)


def evaluate(db_path: Path, predictions: list[dict], gemini: dict[str, str]) -> list[dict]:
    """Each prediction with one outcome per backend; 'gemini' is None until it has an answer."""
    results = []
    for p in predictions:
        answers = {"base": p["base"], "finetuned": p["finetuned"], "gemini": gemini.get(p["question"])}
        results.append({**p, "outcome": {name: answer and score(db_path, p["gold"], answer)
                                         for name, answer in answers.items()}})
    return results


def print_report(results: list[dict]) -> None:
    for name in BACKENDS:
        scored = [r["outcome"][name] for r in results if r["outcome"][name]]
        if scored:
            count = Counter(scored)
            print(f"{name:10} {count['right']:3}/{len(scored)} = {count['right'] / len(scored):6.1%}   "
                  f"wrong rows {count['wrong rows']:3}, failed {count['failed']:3}")
    only = Counter()
    for r in results:
        base, tuned = (r["outcome"][name] == "right" for name in ("base", "finetuned"))
        only[(base, tuned)] += 1
    print(f"\nfine-tuned right and base wrong: {only[(False, True)]}, base right and fine-tuned wrong: {only[(True, False)]}")
    print("\nright per category (base / fine-tuned / gemini)")
    for category in sorted({r["category"] for r in results}):
        rows = [r for r in results if r["category"] == category]
        cells = " / ".join(str(sum(r["outcome"][name] == "right" for r in rows)) for name in BACKENDS)
        print(f"  {category:24} {cells} of {len(rows)}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--ask-gemini", action="store_true", help="ask for the missing Gemini answers first")
    args = ap.parse_args()
    if args.ask_gemini:
        ask_gemini(read_jsonl(DATA_DIR / "test.jsonl"))
    gemini = {r["question"]: r["answer"] for r in read_jsonl(GEMINI_ANSWERS)} if GEMINI_ANSWERS.exists() else {}
    print_report(evaluate(get_settings().db_path, read_jsonl(PREDICTIONS), gemini))
