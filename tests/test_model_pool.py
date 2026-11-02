"""Model pools and quota memory, with fake models (no API calls)."""
import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from langchain_core.exceptions import ModelAPIError, ModelRateLimitError
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.runnables import RunnableLambda

from ida.model_pool import (
    SIGNATURES_KEY,
    AllModelsUnavailable,
    ModelPool,
    QuotaMemory,
    classify,
    for_model,
    next_daily_reset,
    server_error_cooldown,
)

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


def test_server_errors_back_off_exponentially_until_a_success(memory):
    assert [server_error_cooldown(n) for n in range(4)] == [timedelta(minutes=m) for m in (2, 4, 8, 16)]
    assert server_error_cooldown(50) == timedelta(hours=4)

    pool = ModelPool("x", [("a", fails(ModelAPIError("504 DEADLINE_EXCEEDED"))), ("b", ok("b"))], memory)
    for expected in (2, 4, 8):
        memory.block("a", datetime.now(UTC) - timedelta(seconds=1), "rate limited")   # let "a" be tried again
        start = datetime.now(UTC)
        pool.invoke("q")
        blocked = memory.blocked_until("a") - start
        assert timedelta(minutes=expected) <= blocked < timedelta(minutes=expected, seconds=5)
    assert memory.strikes("a") == 3

    memory.block("a", datetime.now(UTC) - timedelta(seconds=1), "rate limited")
    assert ModelPool("x", [("a", ok("a"))], memory).invoke("q") == "a"
    assert memory.strikes("a") == 0                                                   # success clears it


def test_rate_limits_do_not_add_strikes(memory):
    ModelPool("x", [("a", fails(ModelRateLimitError(MINUTE_429))), ("b", ok("b"))], memory).invoke("q")
    assert memory.strikes("a") == 0


def gemini_call(author: str) -> AIMessage:
    """An AI tool call as langchain-google-genai returns it, with its thought signatures."""
    return AIMessage(
        content=[{"type": "text", "text": "checking", "extras": {"signature": "c2ln"}},
                 {"type": "thinking", "thinking": "...", "signature": "c2ln"}],
        additional_kwargs={SIGNATURES_KEY: {"call_1": "c2ln"}, "function_call": {"name": "count"}},
        tool_calls=[{"name": "count", "args": {}, "id": "call_1"}],
        response_metadata={"model_name": author})


def test_for_model_strips_signatures_another_model_wrote():
    msgs = [HumanMessage("q"), gemini_call("gemini-3.5-flash-lite")]
    stripped = for_model(msgs, "gemma-4-26b-a4b-it")[1]
    assert SIGNATURES_KEY not in stripped.additional_kwargs
    assert stripped.additional_kwargs["function_call"] == {"name": "count"}
    assert stripped.content == [{"type": "text", "text": "checking", "extras": {}}]
    assert stripped.tool_calls == msgs[1].tool_calls
    assert SIGNATURES_KEY in msgs[1].additional_kwargs              # the original is not changed
    assert for_model(msgs, "gemini-3.5-flash-lite")[1] is msgs[1]   # its own message keeps them
    assert for_model("plain prompt", "gemma") == "plain prompt"


def test_fallback_model_gets_history_without_foreign_signatures(memory):
    seen = []
    pool = ModelPool("executor", [("gemini-3.5-flash-lite", fails(ModelAPIError("503 UNAVAILABLE"))),
                                  ("gemma-4-26b-a4b-it", RunnableLambda(lambda m: seen.append(m) or "ok"))],
                     memory)
    assert pool.invoke([HumanMessage("q"), gemini_call("gemini-3.5-flash-lite")]) == "ok"
    assert SIGNATURES_KEY not in seen[0][1].additional_kwargs
