"""Optional MongoDB persistence for sanitized audit metadata."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from pymongo import ASCENDING, MongoClient
from pymongo.errors import DuplicateKeyError, PyMongoError, ServerSelectionTimeoutError

from migration_auditor.config import AuditStoreConfig
from migration_auditor.models import StoredRun


class AuditStoreError(RuntimeError):
    """Base error for audit metadata persistence."""


class AuditStoreConnectionError(AuditStoreError):
    """The optional metadata database could not be reached safely."""


class DuplicateRunError(AuditStoreError):
    """A run with the same caller-provided run ID already exists."""


@dataclass(frozen=True)
class PersistResult:
    persisted: bool
    run_id: str
    findings_written: int


class AuditStore(Protocol):
    def persist(self, stored_run: StoredRun) -> PersistResult: ...


class OfflineAuditStore:
    """Default store: keeps no metadata and lets analysis run without MongoDB."""

    def persist(self, stored_run: StoredRun) -> PersistResult:
        return PersistResult(False, stored_run.run.run_id, 0)


class MongoAuditStore:
    """Stores run summaries and sanitized findings in a dedicated metadata database."""

    def __init__(self, client: MongoClient[Any], config: AuditStoreConfig) -> None:
        if not config.enabled:
            raise AuditStoreError("MongoAuditStore requires an enabled audit_store.uri")
        self._client = client
        self._config = config
        database = client[config.database]
        self._runs = database[config.runs_collection]
        self._findings = database[config.findings_collection]

    @classmethod
    def connect(cls, config: AuditStoreConfig) -> MongoAuditStore:
        if not config.enabled:
            raise AuditStoreError("audit metadata persistence is disabled; use OfflineAuditStore")
        client = MongoClient(
            config.uri,
            serverSelectionTimeoutMS=config.server_selection_timeout_ms,
            connectTimeoutMS=config.connect_timeout_ms,
            appname="database-schema-migration-auditor",
        )
        try:
            client.admin.command("ping")
        except (ServerSelectionTimeoutError, PyMongoError) as exc:
            client.close()
            raise AuditStoreConnectionError(
                "could not connect to the audit metadata database"
            ) from exc
        return cls(client, config)

    def close(self) -> None:
        self._client.close()

    def ensure_indexes(self) -> None:
        """Create indexes for duplicate-run protection, lookup, and finite retention."""
        try:
            self._runs.create_index([("run_id", ASCENDING)], unique=True, name="run_id_unique")
            self._runs.create_index(
                [("completed_at", ASCENDING)],
                expireAfterSeconds=self._config.retention_seconds,
                name="audit_run_retention",
            )
            self._findings.create_index([("run_id", ASCENDING)], name="findings_by_run")
            self._findings.create_index(
                [("completed_at", ASCENDING)],
                expireAfterSeconds=self._config.retention_seconds,
                name="finding_retention",
            )
        except PyMongoError as exc:
            raise AuditStoreError("could not create audit metadata indexes") from exc

    def persist(self, stored_run: StoredRun) -> PersistResult:
        run_document = stored_run.run.to_dict() | {
            "started_at": stored_run.run.started_at,
            "completed_at": stored_run.run.completed_at,
        }
        findings = [
            finding.to_dict()
            | {"run_id": stored_run.run.run_id, "completed_at": stored_run.run.completed_at}
            for finding in stored_run.findings
        ]
        try:
            self._runs.insert_one(run_document)
        except DuplicateKeyError as exc:
            raise DuplicateRunError(f"audit run already exists: {stored_run.run.run_id}") from exc
        except PyMongoError as exc:
            raise AuditStoreError("could not store audit run metadata") from exc

        try:
            if findings:
                self._findings.insert_many(findings, ordered=True)
        except PyMongoError as exc:
            # Avoid a misleading partially stored run where the database supports these compensating deletes.
            self._findings.delete_many({"run_id": stored_run.run.run_id})
            self._runs.delete_one({"run_id": stored_run.run.run_id})
            raise AuditStoreError("could not store audit findings") from exc
        return PersistResult(True, stored_run.run.run_id, len(findings))

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        """Return stored metadata only; callers should not log the returned document wholesale."""
        try:
            result = self._runs.find_one({"run_id": run_id}, {"_id": 0})
        except PyMongoError as exc:
            raise AuditStoreError("could not read audit run metadata") from exc
        return result


def build_audit_store(config: AuditStoreConfig) -> AuditStore:
    """Use persistence only when a metadata URI was explicitly configured."""
    if not config.enabled:
        return OfflineAuditStore()
    store = MongoAuditStore.connect(config)
    store.ensure_indexes()
    return store
