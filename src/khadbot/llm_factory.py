"""
llm_factory.py

Singleton responsible for returning a chat model for a given provider.

Provider is resolved from: argument > LLM_PROVIDER env var > 'ollama'
"""

import logging
from dataclasses import replace

from langchain_core.language_models import BaseChatModel

from khadbot.config import get_config
from khadbot.errors import missing_group_message

logger = logging.getLogger(__name__)

_MODEL_PROVIDERS = {"anthropic", "openai", "groq", "ollama"}


class LLMProviderError(Exception):
    """Raised when an LLM provider cannot be initialized or reached."""

    pass


def _check_ollama(model: str, base_url: str) -> None:
    """
    Verify Ollama is running and the requested model is available.

    This is a runtime check, not a config check - it belongs here
    rather than in config.py because it makes a live network call.
    """
    try:
        import httpx

        response = httpx.get(f"{base_url}/api/tags", timeout=5.0)
        response.raise_for_status()
    except httpx.ConnectError as e:
        raise LLMProviderError("Ollama is not running. Start it with: ollama serve") from e
    except httpx.TimeoutException as e:
        raise LLMProviderError("Ollama did not respond within 5 seconds. Is it running?") from e
    except Exception as e:
        raise LLMProviderError(f"Could not reach Ollama: {e}") from e

    available = [m["name"] for m in response.json().get("models", [])]
    if not any(model in m for m in available):
        available_str = ", ".join(available) if available else "none pulled yet"
        raise LLMProviderError(
            f"Model '{model}' not found in Ollama. Pull it with: ollama pull {model}\nAvailable models: {available_str}"
        )


def get_llm(provider: str | None = None, *, role: str | None = None) -> BaseChatModel:
    """
    Initialize and return a chat model for the given provider.

    Provider is resolved from: argument > role override (LLM_PROVIDER_<ROLE>) >
    LLM_PROVIDER env var > 'ollama'. A role may also pin the model name via
    <ROLE>_MODEL (e.g. ANALYST_MODEL); it applies to whichever provider is chosen.

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
    cfg = get_config()
    llm_cfg = cfg.llm

    provider = provider or llm_cfg.role_providers.get(role or "") or llm_cfg.provider.value
    model_override = llm_cfg.role_models.get(role or "")
    if model_override and provider in _MODEL_PROVIDERS:
        llm_cfg = replace(llm_cfg, **{f"{provider}_model": model_override})
    logger.debug(f"Initializing LLM for provider: {provider}")

    if provider == "anthropic":
        if not llm_cfg.anthropic_api_key:
            raise LLMProviderError("ANTHROPIC_API_KEY is not set. Add it to your .env file.")
        try:
            from langchain_anthropic import ChatAnthropic

            logger.info(f"Using Anthropic model: {llm_cfg.anthropic_model}")
            # timeout/stop are optional at runtime; the langchain-anthropic stubs mark them required.
            return ChatAnthropic(  # type: ignore[call-arg]
                model_name=llm_cfg.anthropic_model,
                api_key=llm_cfg.anthropic_api_key,
                temperature=llm_cfg.temperature,
            )
        except Exception as e:
            raise LLMProviderError(
                f"Failed to initialize Anthropic client for model '{llm_cfg.anthropic_model}': {e}"
            ) from e

    elif provider == "openai":
        if not llm_cfg.openai_api_key:
            raise LLMProviderError("OPENAI_API_KEY is not set. Add it to your .env file.")
        try:
            from langchain_openai import ChatOpenAI

            logger.info(f"Using OpenAI model: {llm_cfg.openai_model}")
            return ChatOpenAI(
                model=llm_cfg.openai_model,
                api_key=llm_cfg.openai_api_key,
                temperature=llm_cfg.temperature,
            )
        except ImportError as e:
            raise LLMProviderError(missing_group_message("providers", "The openai provider")) from e
        except Exception as e:
            raise LLMProviderError(f"Failed to initialize OpenAI client for model '{llm_cfg.openai_model}': {e}") from e

    elif provider == "groq":
        if not llm_cfg.groq_api_key:
            raise LLMProviderError("GROQ_API_KEY is not set. Add it to your .env file.")
        try:
            from langchain_groq import ChatGroq

            logger.info(f"Using Groq model: {llm_cfg.groq_model}")
            llm = ChatGroq(
                model=llm_cfg.groq_model,
                api_key=llm_cfg.groq_api_key,
                temperature=llm_cfg.temperature,
            )
            return llm
        except ImportError as e:
            raise LLMProviderError(missing_group_message("providers", "The groq provider")) from e
        except Exception as e:
            raise LLMProviderError(f"Failed to initialize Groq client for model '{llm_cfg.groq_model}': {e}") from e

    elif provider == "ollama":
        _check_ollama(llm_cfg.ollama_model, llm_cfg.ollama_base_url)
        try:
            from langchain_ollama import ChatOllama

            logger.info(f"Using Ollama model: {llm_cfg.ollama_model}")
            return ChatOllama(
                model=llm_cfg.ollama_model,
                base_url=llm_cfg.ollama_base_url,
                temperature=llm_cfg.temperature,
                reasoning=False,  # disable Qwen3 thinking mode
            )
        except ImportError as e:
            raise LLMProviderError(missing_group_message("providers", "The ollama provider")) from e
        except Exception as e:
            raise LLMProviderError(f"Failed to initialize Ollama client for model '{llm_cfg.ollama_model}': {e}") from e

    else:
        raise ValueError(
            f"Unknown provider: '{provider}'. "
            f"Valid options: anthropic, openai, groq, ollama. "
            f"Set LLM_PROVIDER in your .env file."
        )
