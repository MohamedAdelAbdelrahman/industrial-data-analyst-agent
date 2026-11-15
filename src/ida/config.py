"""Settings from environment / .env (see .env.example)."""
import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env")

# The synthetic data ends here (generate_db.py --end); "now" / "last week" are relative to it.
DATA_END = "2026-10-01 00:00:00"


def _env(name: str, default: str) -> str:
    return os.environ.get(name) or default


def _models(name: str, default: str) -> tuple[str, ...]:
    """Comma-separated model pool, tried in order (see ida.model_pool)."""
    return tuple(m.strip() for m in _env(name, default).split(",") if m.strip())


@dataclass(frozen=True)
class Settings:
    llm_provider: str = field(default_factory=lambda: _env("LLM_PROVIDER", "google"))
    # Pools: the first model that is not out of quota answers. Planner/answerer want the strongest
    # models; the executor and SQL writer are called most, so they start with the cheap ones.
    planner_models: tuple[str, ...] = field(default_factory=lambda: _models(
        "PLANNER_MODELS", "gemini-3.5-flash,gemini-3-flash-preview,gemini-3.5-flash-lite,gemini-3.1-flash-lite"))
    executor_models: tuple[str, ...] = field(default_factory=lambda: _models(
        "EXECUTOR_MODELS", "gemini-3.1-flash-lite,gemini-3.5-flash-lite,gemini-3-flash-preview,gemma-4-26b-a4b-it"))
    quota_path: Path = field(default_factory=lambda: Path(_env("QUOTA_PATH", str(ROOT / "data" / "cache" / "quota.json"))))

    sql_backend: str = field(default_factory=lambda: _env("SQL_BACKEND", "llm"))
    sql_llm_models: tuple[str, ...] = field(default_factory=lambda: _models(
        "SQL_LLM_MODELS", "gemini-3.1-flash-lite,gemini-3.5-flash-lite,gemma-4-31b-it"))
    finetuned_sql_model: str = field(default_factory=lambda: _env("FINETUNED_SQL_MODEL", "ida-sql"))
    ollama_url: str = field(default_factory=lambda: _env("OLLAMA_URL", "http://localhost:11434"))
    # seconds per SQL request; Ollama on CPU in a container needs ~45 s for the long schema prompt
    ollama_timeout_s: float = field(default_factory=lambda: float(_env("OLLAMA_TIMEOUT_S", "60")))

    db_path: Path = field(default_factory=lambda: Path(_env("DB_PATH", str(ROOT / "data" / "sensors.db"))))
    schema_path: Path = ROOT / "data" / "schema.sql"
    dataset_dir: Path = field(default_factory=lambda: Path(_env("DATASET_DIR", str(ROOT / "data" / "cache" / "datasets"))))
    artifacts_dir: Path = field(default_factory=lambda: Path(_env("ARTIFACTS_DIR", str(ROOT / "data" / "cache" / "artifacts"))))
    manuals_dir: Path = ROOT / "data" / "manuals"
    index_dir: Path = field(default_factory=lambda: Path(_env("INDEX_DIR", str(ROOT / "data" / "index"))))
    # run_python inside docker compose: run folders on a named volume (see mcp_server/sandbox.py)
    sandbox_work_dir: Path | None = field(
        default_factory=lambda: Path(os.environ["SANDBOX_WORK_DIR"]) if os.environ.get("SANDBOX_WORK_DIR") else None)
    sandbox_volume: str | None = field(default_factory=lambda: os.environ.get("SANDBOX_VOLUME") or None)

    query_timeout_s: float = 10.0
    max_cached_rows: int = 10_000     # rows written to the dataset CSV for run_python


def get_settings() -> Settings:
    return Settings()
