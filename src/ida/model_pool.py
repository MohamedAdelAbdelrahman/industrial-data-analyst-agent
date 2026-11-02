"""Per-role model pools with a quota memory.

The free Gemini tier allows about 20 requests per day per model and often answers 503 "high demand".
A ModelPool tries its models in order and moves on when one fails. QuotaMemory (a small JSON file,
data/cache/quota.json) remembers until when each model is blocked, so later calls and later runs skip
it instead of wasting a request:

    429 per-day quota      -> blocked until the daily reset (midnight Pacific time)
    429 per-minute quota   -> blocked for the retry delay the API suggests (at least 60 s)
    404 model not found    -> blocked for a day
    5xx / timeout          -> blocked for 2 minutes, doubled for each failure in a row (max 4 hours)
    anything else          -> not remembered, but the next model is still tried

Memory is keyed by model name, not role, because the quota belongs to the model: if the planner
exhausts gemini-3.5-flash, the answerer skips it too. The backoff matters because a timeout costs
90 s: a model that is down for hours (gemini-3.8-flash on 2026-10-27) would otherwise cost 90 s every
2 minutes. One successful call clears the failure count. The MCP server (SQL) and the agent are separate
processes sharing the file; a lost write in a race only costs one wasted request.

Gemini attaches "thought signatures" to its tool calls, and the API accepts them only from the model that
made them: Gemma answers 400 to a history that contains one. Falling over in the middle of an executor
step hands one model's history to another, so the pool removes signatures from AI messages that another
model wrote (`for_model`); for Gemini 3 the adapter then sends its documented "skip validation" marker.

    python -m ida.model_pool            # show today's calls and blocked models
    python -m ida.model_pool --reset    # forget everything
"""
import json
import logging
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from langchain_core.exceptions import (
    ModelAPIError,
    ModelConnectionError,
    ModelNotFoundError,
    ModelRateLimitError,
    ModelTimeoutError,
)
from langchain_core.messages import AIMessage
from langchain_core.runnables import Runnable, RunnableConfig

log = logging.getLogger(__name__)

RESET_TZ = ZoneInfo("America/Los_Angeles")     # Gemini daily quotas reset at midnight Pacific
SERVER_ERROR_COOLDOWN = timedelta(minutes=2)
SERVER_ERROR_MAX_COOLDOWN = timedelta(hours=4)
SERVER_ERROR = "server error / timeout"
MIN_RATE_LIMIT_COOLDOWN = timedelta(seconds=60)


def _now() -> datetime:
    return datetime.now(UTC)


def next_daily_reset(now: datetime) -> datetime:
    local = now.astimezone(RESET_TZ)
    midnight = datetime.combine(local.date() + timedelta(days=1), datetime.min.time(), tzinfo=RESET_TZ)
    return midnight.astimezone(UTC)


def server_error_cooldown(strikes: int) -> timedelta:
    """2 min, 4, 8, ... capped at 4 h; `strikes` = server errors in a row before this one."""
    return min(SERVER_ERROR_COOLDOWN * 2 ** min(strikes, 20), SERVER_ERROR_MAX_COOLDOWN)


def classify(exc: Exception, now: datetime, strikes: int = 0) -> tuple[datetime, str] | None:
    """(blocked_until, reason) for errors worth remembering, else None."""
    msg = str(exc)
    if isinstance(exc, ModelRateLimitError) or "429" in msg or "RESOURCE_EXHAUSTED" in msg:
        if "PerDay" in msg:
            return next_daily_reset(now), "daily quota exhausted"
        m = re.search(r"retryDelay['\"]?\s*:\s*['\"]?(\d+(?:\.\d+)?)s", msg)
        delay = timedelta(seconds=float(m.group(1))) if m else MIN_RATE_LIMIT_COOLDOWN
        return now + max(delay, MIN_RATE_LIMIT_COOLDOWN), "rate limited"
    if isinstance(exc, ModelNotFoundError) or "NOT_FOUND" in msg:
        return now + timedelta(days=1), "model not found"
    if isinstance(exc, (ModelAPIError, ModelTimeoutError, ModelConnectionError, TimeoutError)) \
            or re.search(r"\b(500|503|504)\b|UNAVAILABLE|DEADLINE_EXCEEDED", msg):
        return now + server_error_cooldown(strikes), SERVER_ERROR
    return None


class QuotaMemory:
    """{model: {"blocked_until": iso, "reason": str, "strikes": n, "day": "YYYY-MM-DD", "calls": n}}
    in a JSON file. `strikes` counts server errors in a row (for the backoff)."""

    def __init__(self, path: Path):
        self.path = Path(path)

    def _load(self) -> dict[str, dict]:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    def _save(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def blocked_until(self, model: str, now: datetime | None = None) -> datetime | None:
        entry = self._load().get(model, {})
        until = entry.get("blocked_until")
        if until and datetime.fromisoformat(until) > (now or _now()):
            return datetime.fromisoformat(until)
        return None

    def strikes(self, model: str) -> int:
        return self._load().get(model, {}).get("strikes", 0)

    def block(self, model: str, until: datetime, reason: str) -> None:
        data = self._load()
        entry = data.setdefault(model, {})
        entry.update(blocked_until=until.isoformat(), reason=reason)
        if reason == SERVER_ERROR:
            entry["strikes"] = entry.get("strikes", 0) + 1
        self._save(data)

    def record_call(self, model: str, now: datetime | None = None) -> None:
        day = str((now or _now()).astimezone(RESET_TZ).date())     # the quota day, not the local day
        data = self._load()
        entry = data.setdefault(model, {})
        entry["calls"] = entry.get("calls", 0) + 1 if entry.get("day") == day else 1
        entry["day"] = day
        entry.pop("blocked_until", None)
        entry.pop("reason", None)
        entry.pop("strikes", None)
        self._save(data)

    def status(self) -> dict[str, dict]:
        return self._load()

    def reset(self) -> None:
        self.path.unlink(missing_ok=True)


SIGNATURES_KEY = "__gemini_function_call_thought_signatures__"   # langchain-google-genai's map in additional_kwargs
SIGNATURE_FIELDS = ("signature", "thought_signature")
MODEL_BLOCKS = ("thinking", "reasoning")      # another model's thoughts: they carry signatures too


def _strip_block(block: Any) -> Any | None:
    if not isinstance(block, dict):
        return block
    if block.get("type") in MODEL_BLOCKS:
        return None
    out = {k: v for k, v in block.items() if k not in SIGNATURE_FIELDS}
    if isinstance(out.get("extras"), dict):
        out["extras"] = {k: v for k, v in out["extras"].items() if k not in SIGNATURE_FIELDS}
    return out


def for_model(messages: Any, model: str) -> Any:
    """The input for `model`: thought signatures removed from AI messages that another model wrote."""
    if not isinstance(messages, list):
        return messages
    out = []
    for m in messages:
        author = (getattr(m, "response_metadata", None) or {}).get("model_name")
        if isinstance(m, AIMessage) and author != model:
            content = m.content
            if isinstance(content, list):
                content = [b for b in map(_strip_block, content) if b is not None]
            kwargs = {k: v for k, v in m.additional_kwargs.items() if k != SIGNATURES_KEY}
            m = m.model_copy(update={"content": content, "additional_kwargs": kwargs})
        out.append(m)
    return out


class AllModelsUnavailable(RuntimeError):
    pass


class ModelPool(Runnable):
    """Runnable over [(model_name, runnable), ...]; the first available model that succeeds wins."""

    def __init__(self, role: str, members: list[tuple[str, Runnable]], memory: QuotaMemory):
        if not members:
            raise ValueError(f"model pool {role!r} is empty")
        self.role, self.members, self.memory = role, members, memory

    def _available(self) -> list[tuple[str, Runnable]]:
        avail, blocked = [], []
        for name, runnable in self.members:
            until = self.memory.blocked_until(name)
            (blocked if until else avail).append((name, until) if until else (name, runnable))
        if not avail:
            soonest = min(until for _, until in blocked)
            raise AllModelsUnavailable(
                f"every {self.role} model is blocked ({', '.join(n for n, _ in blocked)}); "
                f"the next one frees up at {soonest.astimezone():%Y-%m-%d %H:%M} local time")
        return avail

    def _failed(self, name: str, exc: Exception) -> None:
        now = _now()
        verdict = classify(exc, now, self.memory.strikes(name))
        if verdict:
            self.memory.block(name, *verdict)
        why = f"{verdict[1]}, blocked {verdict[0] - now}" if verdict else type(exc).__name__
        log.warning("[%s] %s failed (%s): %s", self.role, name, why, str(exc)[:200])

    def invoke(self, input: Any, config: RunnableConfig | None = None, **kwargs: Any) -> Any:
        last: Exception | None = None
        for name, runnable in self._available():
            try:
                out = runnable.invoke(for_model(input, name), config, **kwargs)
            except Exception as e:  # noqa: BLE001 -- any failure moves on to the next model
                self._failed(name, e)
                last = e
                continue
            self.memory.record_call(name)
            return out
        raise last  # type: ignore[misc]

    async def ainvoke(self, input: Any, config: RunnableConfig | None = None, **kwargs: Any) -> Any:
        last: Exception | None = None
        for name, runnable in self._available():
            try:
                out = await runnable.ainvoke(for_model(input, name), config, **kwargs)
            except Exception as e:  # noqa: BLE001
                self._failed(name, e)
                last = e
                continue
            self.memory.record_call(name)
            return out
        raise last  # type: ignore[misc]


def default_memory() -> QuotaMemory:
    from ida.config import get_settings
    return QuotaMemory(get_settings().quota_path)


def model_pool(role: str, models: tuple[str, ...], wrap=lambda m: m) -> ModelPool:
    """Pool of chat models; `wrap` adds tools / structured output to each (e.g. lambda m: m.bind_tools(t))."""
    from ida.llm import get_chat_model
    return ModelPool(role, [(name, wrap(get_chat_model(name))) for name in models], default_memory())


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--reset", action="store_true", help="forget all blocks and call counts")
    args = ap.parse_args()
    mem = default_memory()
    if args.reset:
        mem.reset()
        print(f"cleared {mem.path}")
    status = mem.status()
    if not status:
        print("no calls recorded yet")
    today = str(_now().astimezone(RESET_TZ).date())
    for model, e in sorted(status.items()):
        calls = e.get("calls", 0) if e.get("day") == today else 0
        until = mem.blocked_until(model)
        strikes = f", {e['strikes']} in a row" if e.get("strikes") else ""
        state = f"BLOCKED until {until.astimezone():%m-%d %H:%M} ({e.get('reason')}{strikes})" if until else "ok"
        print(f"{model:28} {calls:3} calls today   {state}")
