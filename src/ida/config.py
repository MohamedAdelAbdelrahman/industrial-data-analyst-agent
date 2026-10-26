"""Settings from environment / .env (see .env.example)."""
import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env")


def _env(name: str, default: str) -> str:
    return os.environ.get(name) or default


@dataclass(frozen=True)
class Settings:
    llm_provider: str = field(default_factory=lambda: _env("LLM_PROVIDER", "google"))
    planner_model: str = field(default_factory=lambda: _env("PLANNER_MODEL", "gemini-3.5-flash"))
    executor_model: str = field(default_factory=lambda: _env("EXECUTOR_MODEL", "gemini-3.1-flash-lite"))

    sql_backend: str = field(default_factory=lambda: _env("SQL_BACKEND", "llm"))
    sql_llm_model: str = field(default_factory=lambda: _env("SQL_LLM_MODEL", "gemini-3.1-flash-lite"))
    finetuned_sql_model: str = field(default_factory=lambda: _env("FINETUNED_SQL_MODEL", "ida-sql"))
    ollama_url: str = field(default_factory=lambda: _env("OLLAMA_URL", "http://localhost:11434"))

    db_path: Path = field(default_factory=lambda: Path(_env("DB_PATH", str(ROOT / "data" / "sensors.db"))))
    schema_path: Path = ROOT / "data" / "schema.sql"
    dataset_dir: Path = field(default_factory=lambda: Path(_env("DATASET_DIR", str(ROOT / "data" / "cache" / "datasets"))))
    manuals_dir: Path = ROOT / "data" / "manuals"
    index_dir: Path = field(default_factory=lambda: Path(_env("INDEX_DIR", str(ROOT / "data" / "index"))))

    query_timeout_s: float = 10.0
    max_cached_rows: int = 10_000     # rows written to the dataset CSV for run_python


def get_settings() -> Settings:
    return Settings()
