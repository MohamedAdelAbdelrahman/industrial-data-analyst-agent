"""Guardrails (design doc section 3). Cheap, deterministic checks around the LLM nodes.

- input_guard: length limit, requests to change data, prompt-injection phrases. It runs before any
  LLM call, so an obvious attack costs no free-tier quota. Fuzzy scope questions ("write me a poem")
  are left to the planner's refuse flag, since keyword lists cannot judge topic well.
- wrap_untrusted: tool output (manual text, maintenance descriptions, program stdout) reaches the LLMs
  inside an <untrusted_data> block, and the prompts say never to follow instructions found there.
- injection_flags: the injection check applied to tool output. It only flags; the text still goes
  through (wrapped), because a manual page with odd wording is still evidence.
- output_guard: numbers in the final answer that appear in no tool result and not in the question.

The data layer has its own guards (read-only connection, SQL allow-list, sandbox), so a request that
slips past these heuristics still cannot change data.
"""
import re
from dataclasses import dataclass, field

MAX_QUESTION_CHARS = 1_000

_INJECTION = [re.compile(p, re.IGNORECASE) for p in (
    r"\b(ignore|disregard|forget|override)\b[^.\n]{0,40}\b(previous|prior|above|earlier|all|your|system)\b"
    r"[^.\n]{0,20}\b(instructions?|prompts?|rules?|messages?)\b",
    r"\b(system|developer)\s+(prompt|message)\b",
    r"\byou\s+are\s+now\b",
    r"\bnew\s+instructions?\s*:",
    r"\b(reveal|print|show|repeat|output)\b[^.\n]{0,30}\b(api\s*key|password|secret|credentials?)\b",
    r"<\s*/?\s*(system|untrusted_data)\b",
)]

_POLITE = r"^\s*(?:please\s+|(?:can|could|would|will)\s+you\s+(?:please\s+)?)?"
_DATA = r"(?:alarms?|readings?|machines?|sensors?|maintenance|events?|records?|rows?|tables?|data(?:base)?|thresholds?|status)"
_MODIFY = [re.compile(p, re.IGNORECASE) for p in (
    r"\b(?:drop|truncate|alter)\b[^.?!]{0,30}\b(?:table|database|schema|index|view)s?\b",
    r"\bdelete\s+from\b|\binsert\s+into\b|\bupdate\s+\w+\s+set\b",
    # an imperative that starts the request: "Delete all alarms ...", "Could you set the status ..."
    _POLITE + r"(?:delete|erase|wipe|purge|update|modify|change|edit|insert|set|mark|acknowledge|clear|reset|"
    r"overwrite)\b[^.?!]{0,60}\b" + _DATA + r"\b",
)]

_UNTRUSTED_TAG = re.compile(r"<\s*/?\s*untrusted_data\b[^>]*>?", re.IGNORECASE)
_NUM_ANSWER = re.compile(r"(?<![\w.])(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?")   # "1,234.5", "87.2", "Pump-03"
_NUM_EVIDENCE = re.compile(r"\d+(?:\.\d+)?")
SMALL_INT = 10          # counts like "top 3" or "2 steps" are too common to check


@dataclass
class GuardResult:
    blocked: bool = False
    reason: str = ""
    flags: list[str] = field(default_factory=list)


def injection_flags(text: str) -> list[str]:
    return [f"injection-like text: {m.group(0)[:60]!r}" for p in _INJECTION if (m := p.search(text))]


def input_guard(question: str) -> GuardResult:
    q = question.strip()
    if not q:
        return GuardResult(True, "Please ask a question about the plant's machines, sensors, alarms or "
                                 "maintenance.", ["empty question"])
    if len(q) > MAX_QUESTION_CHARS:
        return GuardResult(True, f"The question is too long ({len(q)} characters, limit "
                                 f"{MAX_QUESTION_CHARS}). Please shorten it.", ["too long"])
    for p in _MODIFY:
        if m := p.search(q):
            return GuardResult(True, "I can only read and analyse the plant data; I can't delete, change or "
                                     "add records.", [f"data modification: {m.group(0).strip()[:60]!r}"])
    if flags := injection_flags(q):
        return GuardResult(True, "The question contains instructions aimed at the assistant itself, so it "
                                 "was not run. Please ask about the plant data or manuals.", flags)
    return GuardResult()


def wrap_untrusted(text: str, source: str) -> str:
    """Delimit tool output as data. Tags inside the text are defused so it cannot close the block early."""
    return f'<untrusted_data source="{source}">\n{_UNTRUSTED_TAG.sub("[tag removed]", text)}\n</untrusted_data>'


def _decimals(token: str) -> int:
    return len(token.split(".")[1]) if "." in token else 0


def unsupported_numbers(answer: str, evidence: str) -> list[str]:
    """Numbers in the answer that no evidence number matches (exactly, or rounded to the answer's precision)."""
    seen = [float(t) for t in _NUM_EVIDENCE.findall(evidence)]
    out = []
    for token in dict.fromkeys(_NUM_ANSWER.findall(answer)):
        n = float(token.replace(",", ""))
        if n.is_integer() and n <= SMALL_INT:
            continue
        d = _decimals(token)
        if not any(abs(round(e, d) - n) < 1e-9 or abs(e - n) < 1e-9 for e in seen):
            out.append(token)
    return out


def output_guard(answer: str, evidence: str) -> GuardResult:
    if numbers := unsupported_numbers(answer, evidence):
        return GuardResult(flags=[f"numbers not found in any tool result: {', '.join(numbers)}"])
    return GuardResult()
