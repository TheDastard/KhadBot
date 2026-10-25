"""
wiring.py

The composition root: builds the stores the pipeline runs on for the configured mode. Local mode (the default) keeps
everything in ``KHADBOT_DATA_DIR`` and SQLite. Hosted mode (``KHADBOT_MODE=hosted``) keeps findings and the read
models in Postgres, and the blobs in the S3-compatible bucket when ``KHADBOT_STORAGE=s3``.

Entry points (the API, the CLI, ``main``) and the services above this module (``jobs``) build their stores here.
Nothing below it reads the mode: the engine takes a store as an argument and never picks one. The hosted libraries
(psycopg, boto3) are imported only on the hosted path that needs them.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from khadbot.config import AuthConfig, DatabaseConfig, EncounterConfig, WarcraftLogsConfig
from khadbot.encounter.annotate import AnnotationCache
from khadbot.encounter.evidence import EncounterData
from khadbot.encounter.findings import FindingsStore, SqliteFindingsStore
from khadbot.encounter.ingest.fetch import RawPageCache
from khadbot.encounter.records_store import ReadModelIndex, ReadModelProjector, RecordsStore
from khadbot.encounter.store import ParquetEventStore
from khadbot.errors import ConfigurationError, missing_group_message
from khadbot.storage import BlobStore, default_blob_store

if TYPE_CHECKING:
    from khadbot.auth.access import ReportAccess
    from khadbot.auth.service import AuthService
    from khadbot.auth.tokens import WclTokens

# ---------------------------------------------------------------------------
# Blob-backed stores
# ---------------------------------------------------------------------------


def blob_store() -> BlobStore:
    """Where the pipeline's objects live: the data directory, or with ``KHADBOT_STORAGE=s3`` the bucket."""
    return default_blob_store()


def raw_cache(blobs: BlobStore | None = None) -> RawPageCache:
    """The raw page cache (``raw/``) in ``blobs``, default the configured blob store."""
    return RawPageCache(blobs or blob_store())


def event_store(blobs: BlobStore | None = None) -> ParquetEventStore:
    """The event store (``events/``) in ``blobs``, default the configured blob store."""
    return ParquetEventStore(blobs or blob_store())


def annotation_cache(blobs: BlobStore | None = None) -> AnnotationCache:
    """The annotation cache (``annotated/``) in ``blobs``, default the configured blob store."""
    return AnnotationCache(blobs or blob_store())


def records_store(blobs: BlobStore | None = None) -> RecordsStore:
    """The persisted pull records (``records/``) in ``blobs``, default the configured blob store."""
    return RecordsStore(blobs or blob_store())


def encounter_data(blobs: BlobStore | None = None) -> EncounterData:
    """Event store, annotation cache and records store in one blob store, authored files from ``config/``."""
    blobs = blobs or blob_store()
    return EncounterData(event_store(blobs), cache=annotation_cache(blobs), records_store=records_store(blobs))


# ---------------------------------------------------------------------------
# Mode-dependent stores
# ---------------------------------------------------------------------------


def findings_store() -> FindingsStore:
    """Postgres in hosted mode (``KHADBOT_MODE=hosted``), else the SQLite file under ``KHADBOT_DATA_DIR``."""
    if DatabaseConfig().hosted:
        try:
            from khadbot.db import PostgresFindingsStore, get_pool  # hosted libraries load only on this path
        except ImportError as exc:
            raise ConfigurationError(missing_group_message("hosted", "Hosted mode")) from exc
        return PostgresFindingsStore(get_pool())
    return SqliteFindingsStore(Path(EncounterConfig().data_dir) / "khadbot.sqlite")


def read_model_projector() -> ReadModelProjector | None:
    """
    The projector ``ProcessPull`` calls after it stores a pull's records: the Postgres one in hosted mode, else
    None (local mode has no read-model tables). Building it opens no connection and no blob store.
    """
    if not DatabaseConfig().hosted:
        return None
    try:
        from khadbot.db import PostgresReadModelProjector
    except ImportError as exc:
        raise ConfigurationError(missing_group_message("hosted", "Hosted mode")) from exc
    return PostgresReadModelProjector(raw_factory=raw_cache)


def read_model_index() -> ReadModelIndex | None:
    """
    What backfill and the records cleanup ask of the read-model tables: the Postgres index in hosted mode, else None
    (local mode has no read-model tables). Building it opens no connection.
    """
    if not DatabaseConfig().hosted:
        return None
    try:
        from khadbot.db import PostgresReadModelIndex
    except ImportError as exc:
        raise ConfigurationError(missing_group_message("hosted", "Hosted mode")) from exc
    return PostgresReadModelIndex()


# ---------------------------------------------------------------------------
# Sign-in and report access (hosted only)
# ---------------------------------------------------------------------------


def auth_service() -> AuthService | None:
    """
    Hosted mode's sign-in service over the Postgres auth tables (``AuthConfig``, the WCL client credentials and
    ``KHADBOT_TOKEN_KEYS``); None in local mode, which has no sign-in. Building it opens the pool, no WCL call.

    Raises:
        ConfigurationError: The ``hosted`` group is missing, or the sign-in settings are incomplete.
    """
    if not DatabaseConfig().hosted:
        return None
    try:
        from khadbot.auth.pg import PostgresAuthStore  # hosted libraries load only on this path
        from khadbot.auth.service import AuthService
        from khadbot.db import get_pool
    except ImportError as exc:
        raise ConfigurationError(missing_group_message("hosted", "Hosted sign-in")) from exc
    try:
        wcl = WarcraftLogsConfig()
    except OSError as exc:
        raise ConfigurationError(f"Hosted sign-in needs the WCL client credentials: {exc}") from None
    return AuthService(
        PostgresAuthStore(get_pool()), AuthConfig(), client_id=wcl.client_id, client_secret=wcl.client_secret
    )


def user_tokens() -> WclTokens | None:
    """What a job's user token resolver draws on: signed-in users' WCL tokens. None in local mode."""
    service = auth_service()
    return service.tokens if service is not None else None


def report_access() -> ReportAccess | None:
    """The access checker every report route uses in hosted mode (``can_read``); None in local mode."""
    service = auth_service()
    return service.access if service is not None else None


__all__ = [
    "annotation_cache",
    "auth_service",
    "blob_store",
    "encounter_data",
    "event_store",
    "findings_store",
    "raw_cache",
    "read_model_index",
    "read_model_projector",
    "records_store",
    "report_access",
    "user_tokens",
]
