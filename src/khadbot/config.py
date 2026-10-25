"""
config.py

ToDo
"""

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


class DeploymentMode(StrEnum):
    LOCAL = "local"  # filesystem blobs, SQLite findings, no auth: today's CLI
    HOSTED = "hosted"  # Postgres (and object storage), chosen by KHADBOT_MODE=hosted


class StorageBackend(StrEnum):
    LOCAL = "local"  # blobs are files under KHADBOT_DATA_DIR
    S3 = "s3"  # blobs are objects in an S3-compatible bucket (Cloudflare R2, SeaweedFS)


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


LLM_ROLES = ("analyst", "conversation")


def _role_env(name: str, *, suffix: bool = False) -> dict[str, str]:
    """Collect ``<name>_<ROLE>`` (or ``<ROLE>_<name>`` when suffix) env vars that are set."""
    out: dict[str, str] = {}
    for role in LLM_ROLES:
        key = f"{role.upper()}_{name}" if suffix else f"{name}_{role.upper()}"
        if val := os.getenv(key):
            out[role] = val
    return out


@dataclass(frozen=True)
class LLMConfig:
    provider: LLMProvider = field(default_factory=lambda: LLMProvider(_optional("LLM_PROVIDER", LLMProvider.OLLAMA)))
    anthropic_api_key: str = field(default_factory=lambda: _optional("ANTHROPIC_API_KEY"))
    anthropic_model: str = field(default_factory=lambda: _optional("ANTHROPIC_MODEL", "claude-sonnet-4-20250514"))
    openai_api_key: str = field(default_factory=lambda: _optional("OPENAI_API_KEY"))
    openai_model: str = field(default_factory=lambda: _optional("OPENAI_MODEL", "gpt-4o"))
    groq_api_key: str = field(default_factory=lambda: _optional("GROQ_API_KEY"))
    groq_model: str = field(default_factory=lambda: _optional("GROQ_MODEL", "llama-3.3-70b-versatile"))
    ollama_model: str = field(default_factory=lambda: _optional("OLLAMA_MODEL", "qwen3:8b"))
    ollama_base_url: str = field(default_factory=lambda: _optional("OLLAMA_BASE_URL", "http://localhost:11434"))
    temperature: float = field(default_factory=lambda: float(_optional("LLM_TEMPERATURE", "0")))
    # Per-role overrides for encounter review ("analyst" | "conversation"); absent roles use the global settings.
    role_providers: dict[str, str] = field(default_factory=lambda: _role_env("LLM_PROVIDER"))
    role_models: dict[str, str] = field(default_factory=lambda: _role_env("MODEL", suffix=True))


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
class EncounterConfig:
    data_dir: str = field(default_factory=lambda: _optional("KHADBOT_DATA_DIR", ".khadbot_data"))
    wcl_max_concurrency: int = 2
    wcl_max_retries: int = 3
    wcl_page_limit: int = 10000
    # Most fights one batched request covers; one 25-fight request has outlasted the 30 s client timeout once,
    # so lower it if timeouts recur.
    wcl_batch_max_fights: int = 25
    cutoff_fraction: float = 0.5
    analyst_max_concurrency: int = 3


@dataclass(frozen=True)
class DatabaseConfig:
    """Hosted-mode Postgres settings. Local mode ignores all of them."""

    mode: DeploymentMode = field(
        default_factory=lambda: DeploymentMode(_optional("KHADBOT_MODE", DeploymentMode.LOCAL))
    )
    database_url: str = field(default_factory=lambda: _optional("DATABASE_URL"))
    pool_min_size: int = field(default_factory=lambda: int(_optional("KHADBOT_DB_POOL_MIN", "1")))
    pool_max_size: int = field(default_factory=lambda: int(_optional("KHADBOT_DB_POOL_MAX", "10")))

    @property
    def hosted(self) -> bool:
        return self.mode is DeploymentMode.HOSTED


@dataclass(frozen=True)
class StorageConfig:
    """
    Where the encounter pipeline's blobs live (``KHADBOT_STORAGE``). S3 credentials are not here: boto3 reads the
    standard ``AWS_*`` environment variables. Local mode ignores every ``KHADBOT_S3_*`` setting.
    """

    backend: StorageBackend = field(default_factory=lambda: StorageBackend(_optional("KHADBOT_STORAGE", "local")))
    s3_bucket: str = field(default_factory=lambda: _optional("KHADBOT_S3_BUCKET"))
    s3_prefix: str = field(default_factory=lambda: _optional("KHADBOT_S3_PREFIX"))
    s3_endpoint: str = field(default_factory=lambda: _optional("KHADBOT_S3_ENDPOINT"))
    s3_region: str = field(default_factory=lambda: _optional("KHADBOT_S3_REGION"))


def _csv(key: str, default: str = "") -> tuple[str, ...]:
    return tuple(part.strip() for part in _optional(key, default).split(",") if part.strip())


@dataclass(frozen=True)
class QueueConfig:
    """
    The hosted job queue's worker settings. ``queues`` are the queues a worker takes jobs from, so one deployment can
    run an ingest worker and a review worker apart. Local mode has no queue and ignores these.
    """

    queues: tuple[str, ...] = field(default_factory=lambda: _csv("KHADBOT_WORKER_QUEUES", "ingest,process,review"))
    concurrency: int = field(default_factory=lambda: int(_optional("KHADBOT_WORKER_CONCURRENCY", "2")))
    # A job that fails on a WCL rate limit or a network error runs again up to ``max_retries`` times, waiting
    # ``retry_backoff_s ** attempt`` seconds before each (3, 9, 27, ... with the default).
    max_retries: int = field(default_factory=lambda: int(_optional("KHADBOT_JOB_MAX_RETRIES", "5")))
    retry_backoff_s: int = field(default_factory=lambda: int(_optional("KHADBOT_JOB_RETRY_BACKOFF_S", "3")))
    # How often a running job writes its progress and checks whether it was cancelled.
    progress_interval_s: float = field(
        default_factory=lambda: float(_optional("KHADBOT_JOB_PROGRESS_INTERVAL_S", "0.5"))
    )
    # How long a stopping worker waits for running jobs before interrupting them, in seconds.
    shutdown_timeout_s: float = field(
        default_factory=lambda: float(_optional("KHADBOT_WORKER_SHUTDOWN_TIMEOUT_S", "60"))
    )


_LOCAL_HOSTS = ("localhost", "127.0.0.1", "[::1]")


@dataclass(frozen=True)
class AuthConfig:
    """
    Hosted-mode sign-in with Warcraft Logs, sessions and report access. Local mode has no sign-in and ignores these.
    The WCL client ID and secret are ``WarcraftLogsConfig``'s; the WCL client must list ``redirect_uri`` exactly.
    """

    # Where WCL sends the browser back: this API's /v1/auth/callback, as registered on the WCL client.
    redirect_uri: str = field(
        default_factory=lambda: _optional("KHADBOT_WCL_REDIRECT_URI", "http://localhost:8000/v1/auth/callback")
    )
    # Where a signed-in browser lands: the web app's URL (or a path on this host). A login's ``next`` path is appended.
    post_login_url: str = field(default_factory=lambda: _optional("KHADBOT_POST_LOGIN_URL", "/"))
    # Browser origins allowed to make state-changing requests (the web app). The API's own origin is always allowed.
    web_origins: tuple[str, ...] = field(
        default_factory=lambda: _csv("KHADBOT_WEB_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000")
    )
    # Fernet keys for the stored WCL tokens, newest first; older keys only decrypt (rotation). Secret.
    token_keys: tuple[str, ...] = field(default_factory=lambda: _csv("KHADBOT_TOKEN_KEYS"), repr=False)
    # OAuth scopes sent to WCL's authorize page; empty sends none, as the recorded sign-in did.
    wcl_scope: str = field(default_factory=lambda: _optional("KHADBOT_WCL_SCOPE"))
    session_idle_hours: float = field(default_factory=lambda: float(_optional("KHADBOT_SESSION_IDLE_HOURS", "336")))
    session_max_days: float = field(default_factory=lambda: float(_optional("KHADBOT_SESSION_MAX_DAYS", "30")))
    login_timeout_s: int = 600  # how long a started sign-in may take before its state expires
    # How long a cached report-access answer stands before WCL is asked again: readable, then not readable.
    access_recheck_s: int = field(default_factory=lambda: int(_optional("KHADBOT_ACCESS_RECHECK_S", "3600")))
    access_denied_recheck_s: int = field(
        default_factory=lambda: int(_optional("KHADBOT_ACCESS_DENIED_RECHECK_S", "300"))
    )

    @property
    def cookie_secure(self) -> bool:
        """Cookies are ``Secure`` unless the API itself is served on plain-HTTP localhost."""
        return not is_local_http(self.redirect_uri)

    @property
    def api_origin(self) -> str:
        """The scheme and host of ``redirect_uri``: this API's own origin."""
        scheme, _, rest = self.redirect_uri.partition("://")
        return f"{scheme}://{rest.split('/', 1)[0]}"


def is_local_http(url: str) -> bool:
    """Whether ``url`` is plain HTTP on this machine (localhost, 127.0.0.1 or [::1]): the only place Secure cookies may be off."""
    if not url.startswith("http://"):
        return False
    host = url[len("http://") :].split("/", 1)[0]
    host = host.rsplit(":", 1)[0] if not host.startswith("[") else host.split("]", 1)[0] + "]"
    return host in _LOCAL_HOSTS


@dataclass(frozen=True)
class SimCConfig:
    binary_path: str = field(default_factory=lambda: _optional("SIMC_BINARY_PATH", "simc"))
    timeout_seconds: int = field(default_factory=lambda: int(_optional("SIMC_TIMEOUT_SECONDS", "120")))
    max_concurrent: int = field(default_factory=lambda: int(_optional("SIMC_MAX_CONCURRENT", "2")))


@dataclass(frozen=True)
class WipefestConfig:
    base_url: str = field(default_factory=lambda: _optional("WIPEFEST_BASE_URL", "http://localhost:3001"))


@dataclass(frozen=True)
class RAGConfig:
    embedding_backend: EmbeddingBackend = field(
        default_factory=lambda: EmbeddingBackend(_optional("EMBEDDING_BACKEND", EmbeddingBackend.LOCAL))
    )
    openai_api_key: str = field(default_factory=lambda: _optional("OPENAI_API_KEY"))
    embedding_model_openai: str = "text-embedding-3-small"
    embedding_model_local: str = field(default_factory=lambda: _optional("LOCAL_EMBEDDING_MODEL", "all-MiniLM-L6-v2"))
    vector_store: VectorStore = field(
        default_factory=lambda: VectorStore(_optional("VECTOR_STORE", VectorStore.CHROMA))
    )
    chroma_persist_dir: str = field(default_factory=lambda: _optional("CHROMA_PERSIST_DIR", ".chroma"))
    pgvector_dsn: str = field(default_factory=lambda: _optional("PGVECTOR_DSN"))
    retrieval_top_k: int = field(default_factory=lambda: int(_optional("RETRIEVAL_TOP_K", "5")))
    similarity_threshold: float = field(default_factory=lambda: float(_optional("SIMILARITY_THRESHOLD", "0.5")))


@dataclass(frozen=True)
class PersonaConfig:
    # Persona ID to activate when no per-user preference is set.
    # Empty string (the default) means no persona — base prompt only.
    # Set KHADBOT_PERSONA=khadgar (or thrall/xalatath) to enable a persona server-wide.
    default_persona_id: str = field(default_factory=lambda: _optional("KHADBOT_PERSONA", ""))


@dataclass(frozen=True)
class ObservabilityConfig:
    langsmith_api_key: str = field(default_factory=lambda: _optional("LANGSMITH_API_KEY"))
    langsmith_project: str = field(default_factory=lambda: _optional("LANGSMITH_PROJECT", "KhadBot"))
    langchain_tracing: bool = field(
        default_factory=lambda: _optional("LANGCHAIN_TRACING_V2", "false").lower() == "true"
    )


@dataclass(frozen=True)
class AppConfig:
    llm: LLMConfig = field(default_factory=LLMConfig)
    raiderio: RaiderIOConfig = field(default_factory=RaiderIOConfig)
    warcraftlogs: WarcraftLogsConfig = field(default_factory=WarcraftLogsConfig)
    simc: SimCConfig = field(default_factory=SimCConfig)
    encounter: EncounterConfig = field(default_factory=EncounterConfig)
    database: DatabaseConfig = field(default_factory=DatabaseConfig)
    storage: StorageConfig = field(default_factory=StorageConfig)
    auth: AuthConfig = field(default_factory=AuthConfig)
    queue: QueueConfig = field(default_factory=QueueConfig)
    wipefest: WipefestConfig = field(default_factory=WipefestConfig)
    rag: RAGConfig = field(default_factory=RAGConfig)
    persona: PersonaConfig = field(default_factory=PersonaConfig)
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
