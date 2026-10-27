"""Per-role model pools with a quota memory.

The free Gemini tier allows about 20 requests per day per model and often answers 503 "high demand".
A ModelPool tries its models in order and moves on when one fails. QuotaMemory (a small JSON file,
data/cache/quota.json) remembers until when each model is blocked, so later calls and later runs skip
it instead of wasting a request:

    429 per-day quota      -> blocked until the daily reset (midnight Pacific time)
    429 per-minute quota   -> blocked for the retry delay the API suggests (at least 60 s)
    404 model not found    -> blocked for a day
    5xx / timeout          -> blocked for 2 minutes
    anything else          -> not remembered, but the next model is still tried

Memory is keyed by model name, not role, because the quota belongs to the model: if the planner
exhausts gemini-3.5-flash, the answerer skips it too. The MCP server (SQL) and the agent are separate
processes sharing the file; a lost write in a race only costs one wasted request.

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
from langchain_core.runnables import Runnable, RunnableConfig

log = logging.getLogger(__name__)

RESET_TZ = ZoneInfo("America/Los_Angeles")     # Gemini daily quotas reset at midnight Pacific
SERVER_ERROR_COOLDOWN = timedelta(minutes=2)
MIN_RATE_LIMIT_COOLDOWN = timedelta(seconds=60)


def _now() -> datetime:
    return datetime.now(UTC)


def next_daily_reset(now: datetime) -> datetime:
    local = now.astimezone(RESET_TZ)
    midnight = datetime.combine(local.date() + timedelta(days=1), datetime.min.time(), tzinfo=RESET_TZ)
    return midnight.astimezone(UTC)


def classify(exc: Exception, now: datetime) -> tuple[datetime, str] | None:
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
        return now + SERVER_ERROR_COOLDOWN, "server error / timeout"
    return None


class QuotaMemory:
    """{model: {"blocked_until": iso, "reason": str, "day": "YYYY-MM-DD", "calls": n}} in a JSON file."""

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

    def block(self, model: str, until: datetime, reason: str) -> None:
        data = self._load()
        data.setdefault(model, {}).update(blocked_until=until.isoformat(), reason=reason)
        self._save(data)

    def record_call(self, model: str, now: datetime | None = None) -> None:
        day = str((now or _now()).astimezone(RESET_TZ).date())     # the quota day, not the local day
        data = self._load()
        entry = data.setdefault(model, {})
        entry["calls"] = entry.get("calls", 0) + 1 if entry.get("day") == day else 1
        entry["day"] = day
        entry.pop("blocked_until", None)
        entry.pop("reason", None)
        self._save(data)

    def status(self) -> dict[str, dict]:
        return self._load()

    def reset(self) -> None:
        self.path.unlink(missing_ok=True)


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
        verdict = classify(exc, _now())
        if verdict:
            self.memory.block(name, *verdict)
        log.warning("[%s] %s failed (%s): %s", self.role, name,
                    verdict[1] if verdict else type(exc).__name__, str(exc)[:200])

    def invoke(self, input: Any, config: RunnableConfig | None = None, **kwargs: Any) -> Any:
        last: Exception | None = None
        for name, runnable in self._available():
            try:
                out = runnable.invoke(input, config, **kwargs)
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
                out = await runnable.ainvoke(input, config, **kwargs)
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
        state = f"BLOCKED until {until.astimezone():%m-%d %H:%M} ({e.get('reason')})" if until else "ok"
        print(f"{model:28} {calls:3} calls today   {state}")
