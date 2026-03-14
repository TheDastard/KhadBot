# src/config.py
from __future__ import annotations

import os
from dataclasses import dataclass, field
from enum import StrEnum


class LLMProvider(StrEnum):
    ANTHROPIC = "anthropic"
    OPENAI = "openai"
    GROQ = "groq"
    OLLAMA = "ollama"


class EmbeddingBackend(StrEnum):
    OPENAI = "openai"
    LOCAL = "local"  # sentence-transformers


class VectorStore(StrEnum):
    CHROMA = "chroma"
    PGVECTOR = "pgvector"


def _require(key: str) -> str:
    val = os.getenv(key)
    if not val:
        raise OSError(f"Required environment variable '{key}' is not set.")
    return val


def _optional(key: str, default: str = "") -> str:
    return os.getenv(key, default)


@dataclass(frozen=True)
class LLMConfig:
    provider: LLMProvider = field(
        default_factory=lambda: LLMProvider(_optional("LLM_PROVIDER", LLMProvider.OLLAMA))
    )
    anthropic_api_key: str = field(default_factory=lambda: _optional("ANTHROPIC_API_KEY"))
    anthropic_model: str = field(
        default_factory=lambda: _optional("ANTHROPIC_MODEL", "claude-sonnet-4-20250514")
    )
    openai_api_key: str = field(default_factory=lambda: _optional("OPENAI_API_KEY"))
    openai_model: str = field(default_factory=lambda: _optional("OPENAI_MODEL", "gpt-4o"))
    groq_api_key: str = field(default_factory=lambda: _optional("GROQ_API_KEY"))
    groq_model: str = field(
        default_factory=lambda: _optional("GROQ_MODEL", "llama-3.3-70b-versatile")
    )
    ollama_model: str = field(default_factory=lambda: _optional("OLLAMA_MODEL", "qwen3:8b"))
    ollama_base_url: str = field(
        default_factory=lambda: _optional("OLLAMA_BASE_URL", "http://localhost:11434")
    )
    temperature: float = field(default_factory=lambda: float(_optional("LLM_TEMPERATURE", "0")))


@dataclass(frozen=True)
class RaiderIOConfig:
    base_url: str = "https://raider.io/api/v1"
    # No auth required for public endpoints; reserved for future use
    api_key: str = field(default_factory=lambda: _optional("RAIDERIO_API_KEY"))


@dataclass(frozen=True)
class WarcraftLogsConfig:
    client_id: str = field(default_factory=lambda: _require("WARCRAFTLOGS_CLIENT_ID"))
    client_secret: str = field(default_factory=lambda: _require("WARCRAFTLOGS_CLIENT_SECRET"))
    api_url: str = "https://www.warcraftlogs.com/api/v2/client"
    oauth_url: str = "https://www.warcraftlogs.com/oauth/token"


@dataclass(frozen=True)
class SimCConfig:
    binary_path: str = field(default_factory=lambda: _optional("SIMC_BINARY_PATH", "simc"))
    timeout_seconds: int = field(
        default_factory=lambda: int(_optional("SIMC_TIMEOUT_SECONDS", "120"))
    )
    max_concurrent: int = field(default_factory=lambda: int(_optional("SIMC_MAX_CONCURRENT", "2")))


@dataclass(frozen=True)
class WipefestConfig:
    base_url: str = field(
        default_factory=lambda: _optional("WIPEFEST_BASE_URL", "http://localhost:3001")
    )


@dataclass(frozen=True)
class RAGConfig:
    embedding_backend: EmbeddingBackend = field(
        default_factory=lambda: EmbeddingBackend(
            _optional("EMBEDDING_BACKEND", EmbeddingBackend.LOCAL)
        )
    )
    openai_api_key: str = field(default_factory=lambda: _optional("OPENAI_API_KEY"))
    embedding_model_openai: str = "text-embedding-3-small"
    embedding_model_local: str = field(
        default_factory=lambda: _optional("LOCAL_EMBEDDING_MODEL", "all-MiniLM-L6-v2")
    )
    vector_store: VectorStore = field(
        default_factory=lambda: VectorStore(_optional("VECTOR_STORE", VectorStore.CHROMA))
    )
    chroma_persist_dir: str = field(
        default_factory=lambda: _optional("CHROMA_PERSIST_DIR", ".chroma")
    )
    pgvector_dsn: str = field(default_factory=lambda: _optional("PGVECTOR_DSN"))
    retrieval_top_k: int = field(default_factory=lambda: int(_optional("RETRIEVAL_TOP_K", "5")))
    similarity_threshold: float = field(
        default_factory=lambda: float(_optional("SIMILARITY_THRESHOLD", "0.5"))
    )


@dataclass(frozen=True)
class ObservabilityConfig:
    langsmith_api_key: str = field(default_factory=lambda: _optional("LANGSMITH_API_KEY"))
    langsmith_project: str = field(
        default_factory=lambda: _optional("LANGSMITH_PROJECT", "wow-coaching-agent")
    )
    langchain_tracing: bool = field(
        default_factory=lambda: _optional("LANGCHAIN_TRACING_V2", "false").lower() == "true"
    )


@dataclass(frozen=True)
class AppConfig:
    llm: LLMConfig = field(default_factory=LLMConfig)
    raiderio: RaiderIOConfig = field(default_factory=RaiderIOConfig)
    warcraftlogs: WarcraftLogsConfig = field(default_factory=WarcraftLogsConfig)
    simc: SimCConfig = field(default_factory=SimCConfig)
    wipefest: WipefestConfig = field(default_factory=WipefestConfig)
    rag: RAGConfig = field(default_factory=RAGConfig)
    observability: ObservabilityConfig = field(default_factory=ObservabilityConfig)


# ---------------------------------------------------------------------------
# Module-level singleton — import this everywhere else in the codebase.
# Instantiation is deferred: importing config.py does NOT read env vars.
# Call get_config() to trigger validation.
# ---------------------------------------------------------------------------

_config: AppConfig | None = None


def get_config() -> AppConfig:
    global _config
    if _config is None:
        _config = AppConfig()
    return _config


def reset_config() -> None:
    """Force re-read from environment. Used in tests only."""
    global _config
    _config = None
