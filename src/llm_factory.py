"""
llm_factory.py

Singleton responsible for returning a chat model for a given provider.

Provider is resolved from: argument > LLM_PROVIDER env var > 'ollama'
"""

import logging
import os

from langchain_core.language_models import BaseChatModel

logger = logging.getLogger(__name__)


class LLMProviderError(Exception):
    """Raised when an LLM provider cannot be initialized or reached."""

    pass


def _check_anthropic_key() -> str:
    """Verify ANTHROPIC_API_KEY is set and return it."""
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise LLMProviderError("ANTHROPIC_API_KEY is not set. Add it to your .env file.")
    return api_key


def _check_openai_key() -> str:
    """Verify OPENAI_API_KEY is set and return it."""
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise LLMProviderError("OPENAI_API_KEY is not set. Add it to your .env file.")
    return api_key


def _check_groq_key() -> str:
    """Verify GROQ_API_KEY is set and return it."""
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        raise LLMProviderError(
            "GROQ_API_KEY is not set. Get a key at console.groq.com and add it to your .env file."
        )
    return api_key


def _check_ollama(model: str) -> None:
    """Verify Ollama is running and the requested model is available."""
    try:
        import httpx

        base_url = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
        response = httpx.get(f"{base_url}/api/tags", timeout=5.0)
        response.raise_for_status()
    except httpx.ConnectError:
        raise LLMProviderError("Ollama is not running. Start it with: ollama serve")
    except httpx.TimeoutException:
        raise LLMProviderError("Ollama did not respond within 5 seconds. Is it running?")
    except Exception as e:
        raise LLMProviderError(f"Could not reach Ollama: {e}")

    available = [m["name"] for m in response.json().get("models", [])]
    # Normalize: Ollama may store "qwen3:8b" but also "qwen3:8b-instruct" etc.
    if not any(model in m for m in available):
        available_str = ", ".join(available) if available else "none pulled yet"
        raise LLMProviderError(
            f"Model '{model}' not found in Ollama. "
            f"Pull it with: ollama pull {model}\n"
            f"Available models: {available_str}"
        )


def get_llm(provider: str = None) -> BaseChatModel:
    """
    Initialize and return a chat model for the given provider.

    Provider is resolved from: argument > LLM_PROVIDER env var > 'ollama'

    Supported providers:
      - "anthropic"  — Claude Sonnet; production quality eval (BYOK)
      - "openai"     — GPT-4o; production quality eval (BYOK)
      - "groq"       — Llama 3.3 70B via Groq free tier; integration tests
      - "ollama"     — Local open-weight model via Ollama; inner dev loop (free)

    Raises:
        LLMProviderError: If the provider cannot be reached, credentials are
            missing, or the requested model is not available.
        ValueError: If an unrecognized provider name is given.
    """
    provider = provider or os.getenv("LLM_PROVIDER", "ollama")
    logger.debug(f"Initializing LLM for provider: {provider}")

    if provider == "anthropic":
        _check_anthropic_key()
        model = os.getenv("LLM_MODEL", "claude-sonnet-4-20250514")
        try:
            from langchain_anthropic import ChatAnthropic

            llm = ChatAnthropic(model=model, temperature=0)
            logger.info(f"Using Anthropic model: {model}")
            return llm
        except Exception as e:
            raise LLMProviderError(
                f"Failed to initialize Anthropic client for model '{model}': {e}"
            )

    elif provider == "openai":
        _check_openai_key()
        model = os.getenv("LLM_MODEL", "gpt-4o")
        try:
            from langchain_openai import ChatOpenAI

            llm = ChatOpenAI(model=model, temperature=0)
            logger.info(f"Using OpenAI model: {model}")
            return llm
        except Exception as e:
            raise LLMProviderError(f"Failed to initialize OpenAI client for model '{model}': {e}")

    elif provider == "groq":
        _check_groq_key()
        model = os.getenv("LLM_MODEL", "llama-3.3-70b-versatile")
        try:
            from langchain_groq import ChatGroq

            llm = ChatGroq(model=model, temperature=0)
            logger.info(f"Using Groq model: {model}")
            return llm
        except Exception as e:
            raise LLMProviderError(f"Failed to initialize Groq client for model '{model}': {e}")

    elif provider == "ollama":
        model = os.getenv("LLM_MODEL", "qwen3:8b")
        _check_ollama(model)
        from langchain_ollama import ChatOllama

        logger.info(f"Using Ollama model: {model}")
        return ChatOllama(
            model=model,
            temperature=0,
            model_kwargs={"think": False},  # disable Qwen3 thinking mode
        )

    else:
        available = ["anthropic", "openai", "groq", "ollama"]
        raise ValueError(
            f"Unknown provider: '{provider}'. "
            f"Valid options: {', '.join(available)}. "
            f"Set LLM_PROVIDER in your .env file."
        )
