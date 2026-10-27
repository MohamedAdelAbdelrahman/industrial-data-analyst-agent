"""Model pools and quota memory, with fake models (no API calls)."""
import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from langchain_core.exceptions import ModelAPIError, ModelRateLimitError
from langchain_core.runnables import RunnableLambda

from ida.model_pool import AllModelsUnavailable, ModelPool, QuotaMemory, classify, next_daily_reset

NOW = datetime(2026, 10, 27, 12, 0, tzinfo=UTC)       # 05:00 in California (PDT)
DAILY_429 = ("429 RESOURCE_EXHAUSTED. Quota exceeded for metric generate_content_free_tier_requests, "
             "quotaId: GenerateRequestsPerDayPerProjectPerModel-FreeTier")
MINUTE_429 = "429 RESOURCE_EXHAUSTED ... GenerateRequestsPerMinutePerProjectPerModel ... 'retryDelay': '37s'"


def ok(answer):
    return RunnableLambda(lambda _: answer)


def fails(exc):
    def run(_):
        raise exc
    return RunnableLambda(run)


@pytest.fixture
def memory(tmp_path):
    return QuotaMemory(tmp_path / "quota.json")


def test_daily_reset_is_next_pacific_midnight():
    assert next_daily_reset(NOW) == datetime(2026, 10, 28, 7, 0, tzinfo=UTC)      # PDT, UTC-7
    winter = datetime(2026, 12, 1, 12, 0, tzinfo=UTC)
    assert next_daily_reset(winter) == datetime(2026, 12, 2, 8, 0, tzinfo=UTC)    # PST, UTC-8


def test_classify():
    assert classify(ModelRateLimitError(DAILY_429), NOW) == (next_daily_reset(NOW), "daily quota exhausted")
    assert classify(ModelRateLimitError(MINUTE_429), NOW) == (NOW + timedelta(seconds=60), "rate limited")
    assert classify(ModelRateLimitError("429 'retryDelay': '90s'"), NOW)[0] == NOW + timedelta(seconds=90)
    assert classify(ModelAPIError("503 UNAVAILABLE high demand"), NOW)[1] == "server error / timeout"
    assert classify(ValueError("bad schema"), NOW) is None


def test_falls_over_and_remembers_daily_quota(memory):
    pool = ModelPool("planner", [("a", fails(ModelRateLimitError(DAILY_429))), ("b", ok("from b"))], memory)
    assert pool.invoke("q") == "from b"
    assert memory.blocked_until("a") is not None
    assert memory.status()["b"]["calls"] == 1

    # a fresh pool (= the next run) skips "a" without calling it
    calls = []
    pool2 = ModelPool("planner", [("a", RunnableLambda(lambda _: calls.append("a"))), ("b", ok("b again"))],
                      memory)
    assert pool2.invoke("q") == "b again" and calls == []


def test_unclassified_error_tries_next_but_is_not_remembered(memory):
    pool = ModelPool("x", [("a", fails(ValueError("boom"))), ("b", ok("b"))], memory)
    assert pool.invoke("q") == "b"
    assert memory.blocked_until("a") is None


def test_all_fail_raises_last_error(memory):
    pool = ModelPool("x", [("a", fails(ModelAPIError("503"))), ("b", fails(ValueError("last")))], memory)
    with pytest.raises(ValueError, match="last"):
        pool.invoke("q")


def test_all_blocked_fails_fast(memory):
    memory.block("a", datetime.now(UTC) + timedelta(hours=1), "daily quota exhausted")
    with pytest.raises(AllModelsUnavailable, match="every x model is blocked"):
        ModelPool("x", [("a", ok("a"))], memory).invoke("q")


def test_expired_block_and_success_unblock(memory):
    memory.block("a", datetime.now(UTC) - timedelta(minutes=1), "rate limited")
    assert memory.blocked_until("a") is None
    assert ModelPool("x", [("a", ok("a"))], memory).invoke("q") == "a"
    assert "blocked_until" not in memory.status()["a"]


def test_call_counter_resets_each_quota_day(memory):
    memory.record_call("a", NOW)
    memory.record_call("a", NOW)
    assert memory.status()["a"]["calls"] == 2
    memory.record_call("a", NOW + timedelta(days=1))
    assert memory.status()["a"]["calls"] == 1


def test_async(memory):
    pool = ModelPool("x", [("a", fails(ModelAPIError("504 DEADLINE_EXCEEDED"))), ("b", ok("b"))], memory)
    assert asyncio.run(pool.ainvoke("q")) == "b"
    assert memory.status()["a"]["reason"] == "server error / timeout"
