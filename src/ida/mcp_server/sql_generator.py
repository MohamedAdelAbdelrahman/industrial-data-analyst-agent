"""Natural language -> SQL. Backends are swappable via SQL_BACKEND (llm | finetuned).

build_prompt() is the single prompt format: the fine-tuning data (Step 6b) uses it too,
so the fine-tuned model sees exactly the same input at training and inference time.
"""
import re
from functools import lru_cache
from typing import Protocol

import httpx

from ida.config import DATA_END, Settings, get_settings

INSTRUCTIONS = f"""You translate questions about an industrial sensor database into one SQLite SELECT query.
Rules:
- Use only the tables and columns in the schema.
- Timestamps are TEXT 'YYYY-MM-DD HH:MM:SS' in UTC; compare them as strings or with date()/datetime().
- The data ends at {DATA_END}. Treat that as "now" ("today", "last week", "currently") and never use
  date('now') or CURRENT_TIMESTAMP.
- readings.value is NULL when quality = 'bad'.
- An alarm is active when cleared_at IS NULL.
- Return only the SQL, no explanation."""


def build_prompt(schema: str, question: str) -> str:
    return f"{INSTRUCTIONS}\n\n### Schema\n{schema}\n\n### Question\n{question}\n\n### SQL\n"


def extract_sql(text: str) -> str:
    """Strip markdown fences / chatter around the SQL a model returns."""
    m = re.search(r"```(?:sql)?\s*(.*?)```", text, re.DOTALL | re.IGNORECASE)
    if m:
        text = m.group(1)
    m = re.search(r"\b(WITH|SELECT)\b.*", text, re.DOTALL | re.IGNORECASE)
    return (m.group(0) if m else text).strip()


class SQLGenerator(Protocol):
    name: str

    def generate(self, question: str) -> str: ...


@lru_cache
def load_schema(path: str) -> str:
    with open(path, encoding="utf-8") as f:
        return f.read()


class LLMSQLGenerator:
    def __init__(self, settings: Settings):
        from ida.model_pool import model_pool
        self.name = f"llm:{settings.sql_llm_models[0]}"
        self.schema = load_schema(str(settings.schema_path))
        self.model = model_pool("sql", settings.sql_llm_models)

    def generate(self, question: str) -> str:
        msg = self.model.invoke(build_prompt(self.schema, question))
        self.name = f"llm:{msg.response_metadata.get('model_name', self.name.split(':', 1)[1])}"   # who answered
        return extract_sql(msg.text)


class OllamaSQLGenerator:
    """Fine-tuned model served by Ollama (Step 6b)."""

    def __init__(self, settings: Settings):
        self.name = f"finetuned:{settings.finetuned_sql_model}"
        self.schema = load_schema(str(settings.schema_path))
        self.model = settings.finetuned_sql_model
        self.url = settings.ollama_url.rstrip("/") + "/api/generate"

    def generate(self, question: str) -> str:
        r = httpx.post(self.url, timeout=60, json={
            "model": self.model, "prompt": build_prompt(self.schema, question),
            "stream": False, "raw": False, "options": {"temperature": 0}})
        r.raise_for_status()
        return extract_sql(r.json()["response"])


def make_generator(settings: Settings | None = None) -> SQLGenerator:
    settings = settings or get_settings()
    if settings.sql_backend == "llm":
        return LLMSQLGenerator(settings)
    if settings.sql_backend == "finetuned":
        return OllamaSQLGenerator(settings)
    raise ValueError(f"Unknown SQL_BACKEND: {settings.sql_backend!r}")
