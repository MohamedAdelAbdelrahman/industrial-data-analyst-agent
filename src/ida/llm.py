"""Chat model factory, so the provider is a config switch (LLM_PROVIDER)."""
from langchain_core.language_models import BaseChatModel

from ida.config import get_settings

# Fail fast: a model pool (ida.model_pool) moves to the next model instead of retrying this one.
TIMEOUT_S = 90
MAX_RETRIES = 0


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
