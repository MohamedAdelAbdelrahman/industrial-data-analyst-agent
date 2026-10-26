"""Chat model factory, so the provider is a config switch (LLM_PROVIDER)."""
from langchain_core.language_models import BaseChatModel

from ida.config import get_settings

# Fail fast instead of silently retrying for minutes (free-tier rate limits return 429s).
TIMEOUT_S = 60
MAX_RETRIES = 2


def get_chat_model(model: str, temperature: float = 0.0) -> BaseChatModel:
    provider = get_settings().llm_provider
    if provider == "google":
        from langchain_google_genai import ChatGoogleGenerativeAI
        return ChatGoogleGenerativeAI(model=model, temperature=temperature,
                                      timeout=TIMEOUT_S, max_retries=MAX_RETRIES)
    if provider == "anthropic":
        from langchain_anthropic import ChatAnthropic  # pip install -e ".[anthropic]"
        return ChatAnthropic(model=model, temperature=temperature,
                             timeout=TIMEOUT_S, max_retries=MAX_RETRIES)
    raise ValueError(f"Unknown LLM_PROVIDER: {provider!r}")
