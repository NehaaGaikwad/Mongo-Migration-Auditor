from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pymongo.errors import DuplicateKeyError, ServerSelectionTimeoutError

from migration_auditor.audit_store import (
    AuditStoreConnectionError,
    DuplicateRunError,
    MongoAuditStore,
    OfflineAuditStore,
    build_audit_store,
)
from migration_auditor.config import AuditStoreConfig
from migration_auditor.models import AuditRun, Confidence, Finding, RunStatus, Severity, StoredRun


class FakeCollection:
    def __init__(self) -> None:
        self.documents: list[dict[str, object]] = []
        self.indexes: list[tuple[object, dict[str, object]]] = []

    def create_index(self, fields: object, **kwargs: object) -> None:
        self.indexes.append((fields, kwargs))

    def insert_one(self, document: dict[str, object]) -> None:
        if any(item.get("run_id") == document.get("run_id") for item in self.documents):
            raise DuplicateKeyError("duplicate run")
        self.documents.append(document)

    def insert_many(self, documents: list[dict[str, object]], ordered: bool) -> None:
        assert ordered is True
        self.documents.extend(documents)

    def find_one(
        self, query: dict[str, object], projection: dict[str, int]
    ) -> dict[str, object] | None:
        for document in self.documents:
            if document.get("run_id") == query.get("run_id"):
                return {key: value for key, value in document.items() if key != "_id"}
        return None

    def delete_many(self, query: dict[str, object]) -> None:
        self.documents[:] = [
            item for item in self.documents if item.get("run_id") != query.get("run_id")
        ]

    def delete_one(self, query: dict[str, object]) -> None:
        self.delete_many(query)


class FakeDatabase:
    def __init__(self) -> None:
        self.collections: dict[str, FakeCollection] = {}

    def __getitem__(self, name: str) -> FakeCollection:
        return self.collections.setdefault(name, FakeCollection())


class FakeClient:
    def __init__(self) -> None:
        self.database = FakeDatabase()
        self.closed = False

    def __getitem__(self, name: str) -> FakeDatabase:
        return self.database

    def close(self) -> None:
        self.closed = True


def sample_stored_run() -> StoredRun:
    finding = Finding.create(
        rule_id="MONGO-DATA-002",
        title="Destructive operation",
        severity=Severity.HIGH,
        confidence=Confidence.HIGH,
        explanation="A supported destructive operation was recognized.",
        evidence=("recognized operation: drop",),
        remediation="Confirm a recovery plan before deployment.",
        documentation_url=None,
        suppressible=False,
    )
    run = AuditRun(
        run_id="run-for-store-test",
        analyzer_version="0.1.0",
        started_at=datetime(2026, 10, 21, 8, 0, tzinfo=UTC),
        completed_at=datetime(2026, 10, 21, 8, 1, tzinfo=UTC),
        status=RunStatus.COMPLETED,
        config_fingerprint="b" * 64,
        files_analyzed=1,
        findings_count=1,
    )
    return StoredRun(run=run, findings=(finding,))


def enabled_config() -> AuditStoreConfig:
    return AuditStoreConfig(uri="mongodb://localhost:27017")


def test_offline_store_is_the_default_and_writes_nothing() -> None:
    stored_run = sample_stored_run()

    assert isinstance(build_audit_store(AuditStoreConfig()), OfflineAuditStore)
    assert OfflineAuditStore().persist(stored_run).persisted is False


def test_mongo_store_creates_indexes_persists_sanitized_contract_and_rejects_duplicates() -> None:
    client = FakeClient()
    store = MongoAuditStore(client, enabled_config())  # type: ignore[arg-type]
    store.ensure_indexes()

    result = store.persist(sample_stored_run())

    assert result.persisted is True
    assert result.findings_written == 1
    assert store.get_run("run-for-store-test")["config_fingerprint"] == "b" * 64
    assert len(client.database["audit_runs"].indexes) == 2
    assert len(client.database["audit_findings"].indexes) == 2
    with pytest.raises(DuplicateRunError):
        store.persist(sample_stored_run())


def test_connection_failure_does_not_expose_uri(monkeypatch: pytest.MonkeyPatch) -> None:
    class FailingClient:
        def __init__(self, *args: object, **kwargs: object) -> None:
            self.admin = self

        def command(self, command: str) -> None:
            raise ServerSelectionTimeoutError("internal database host detail")

        def close(self) -> None:
            pass

    monkeypatch.setattr("migration_auditor.audit_store.MongoClient", FailingClient)

    with pytest.raises(AuditStoreConnectionError, match="could not connect") as error:
        MongoAuditStore.connect(AuditStoreConfig(uri="mongodb://user:password@localhost:27017"))

    assert "password" not in str(error.value)
