import json
import os
from pathlib import Path
from uuid import uuid4

import pytest
from bson.int64 import Int64
from pymongo import MongoClient
from pymongo.errors import OperationFailure, ServerSelectionTimeoutError

from migration_auditor.analysis import AnalysisReport
from migration_auditor.config import PreflightConfig
from migration_auditor.preflight import PreflightCheck, run_preflight


class FakeCollection:
    def __init__(self, groups=None, documents=()):
        self.groups = groups or []
        self.documents = documents

    def aggregate(self, pipeline, **kwargs):
        assert "$group" in pipeline[1]
        return self.groups

    def find(self, query, projection, **kwargs):
        return FakeCursor(self.documents)


class FakeCursor:
    def __init__(self, documents):
        self.documents = documents

    def limit(self, count):
        return self.documents[:count]


class FakeDatabase:
    def __init__(self, collection):
        self.collection = collection

    def command(self, command):
        assert command == "ping"

    def __getitem__(self, name):
        return self.collection


class FakeClient:
    database = None

    def __init__(self, *args, **kwargs):
        pass

    def __getitem__(self, name):
        return self.database

    def close(self):
        pass


def _manifest(tmp_path: Path, operation: dict) -> tuple[Path, ...]:
    path = tmp_path / "001.migration.json"
    path.write_text(json.dumps({"version": 1, "collection": "users", "operations": [operation]}))
    return (path,)


def _config():
    return PreflightConfig(uri="mongodb://fake", database="staging", max_documents=2)


def test_skipped_preflight_does_not_change_static_error_status():
    report = AnalysisReport(files=(), findings=(), errors=("static analysis failed",),
                             preflight_checks=(PreflightCheck("*", "connection", "skipped", "disabled"),))

    assert report.to_dict()["status"] == "error"


@pytest.mark.parametrize("check_status", ["incomplete", "unknown"])
def test_incomplete_or_unknown_preflight_status_remains_incomplete(check_status):
    report = AnalysisReport(files=(), findings=(), errors=(),
                            preflight_checks=(PreflightCheck("users", "operations[1]", check_status, "manual"),))

    assert not report.successful
    assert report.to_dict()["status"] == "incomplete"


def test_unique_preflight_reports_sampled_duplicate_groups(tmp_path, monkeypatch):
    from migration_auditor import preflight

    FakeClient.database = FakeDatabase(FakeCollection(groups=[{"_id": {"email": "hidden"}, "count": 2}]))
    monkeypatch.setattr(preflight, "MongoClient", FakeClient)
    findings, checks = run_preflight(_manifest(tmp_path, {"op": "create_index", "keys": {"email": 1}, "unique": True}), _config())
    assert findings[0].rule_id == "MONGO-PREFLIGHT-001"
    assert "hidden" not in str(findings[0].to_dict())
    assert checks[0].status == "incomplete"


def test_validator_preflight_reports_missing_required_field(tmp_path, monkeypatch):
    from migration_auditor import preflight

    FakeClient.database = FakeDatabase(FakeCollection(documents=[{"_id": 1}]))
    monkeypatch.setattr(preflight, "MongoClient", FakeClient)
    operation = {"op": "updateValidator", "validator": {"$jsonSchema": {"required": ["email"]}}}
    findings, checks = run_preflight(_manifest(tmp_path, operation), _config())
    assert findings[0].rule_id == "MONGO-PREFLIGHT-002"
    assert checks[0].status == "sampled"


def test_unsupported_validator_is_unknown(tmp_path, monkeypatch):
    from migration_auditor import preflight

    FakeClient.database = FakeDatabase(FakeCollection())
    monkeypatch.setattr(preflight, "MongoClient", FakeClient)
    operation = {"op": "updateValidator", "validator": {"$where": "secret"}}
    findings, checks = run_preflight(_manifest(tmp_path, operation), _config())
    assert findings == ()
    assert checks[0].status == "unknown"


def test_supported_root_object_bson_type_is_checked(tmp_path, monkeypatch):
    from migration_auditor import preflight

    FakeClient.database = FakeDatabase(FakeCollection(documents=[{"_id": 1}]))
    monkeypatch.setattr(preflight, "MongoClient", FakeClient)
    operation = {"op": "updateValidator", "validator": {"$jsonSchema": {"bsonType": "object"}}}
    findings, checks = run_preflight(_manifest(tmp_path, operation), _config())

    assert findings == ()
    assert checks[0].status == "sampled"


@pytest.mark.parametrize("root_type", ["array", None])
def test_unsupported_root_bson_type_is_unknown(tmp_path, monkeypatch, root_type):
    from migration_auditor import preflight

    FakeClient.database = FakeDatabase(FakeCollection(documents=[{"_id": 1}]))
    monkeypatch.setattr(preflight, "MongoClient", FakeClient)
    operation = {"op": "updateValidator", "validator": {"$jsonSchema": {"bsonType": root_type}}}
    findings, checks = run_preflight(_manifest(tmp_path, operation), _config())

    assert findings == ()
    assert checks[0].status == "unknown"


@pytest.mark.parametrize(
    ("expected", "value"),
    [("int", Int64(7)), ("long", 7)],
)
def test_bson_int32_and_int64_are_not_interchangeable(tmp_path, monkeypatch, expected, value):
    from migration_auditor import preflight

    FakeClient.database = FakeDatabase(FakeCollection(documents=[{"count": value}]))
    monkeypatch.setattr(preflight, "MongoClient", FakeClient)
    operation = {"op": "updateValidator", "validator": {"$jsonSchema": {"properties": {"count": {"bsonType": expected}}}}}

    findings, _ = run_preflight(_manifest(tmp_path, operation), _config())

    assert findings[0].rule_id == "MONGO-PREFLIGHT-002"


def test_compound_unique_index_checks_combination(tmp_path, monkeypatch):
    from migration_auditor import preflight

    collection = FakeCollection(groups=[{"_id": {"tenant": 1, "email": 2}, "count": 2}])
    FakeClient.database = FakeDatabase(collection)
    monkeypatch.setattr(preflight, "MongoClient", FakeClient)
    findings, _ = run_preflight(_manifest(tmp_path, {"op": "create_index", "keys": {"tenant": 1, "email": 1}, "unique": True}), _config())
    assert findings[0].field == "tenant, email"


def test_compliant_validator_is_reported_as_bounded_sample(tmp_path, monkeypatch):
    from migration_auditor import preflight

    FakeClient.database = FakeDatabase(FakeCollection(documents=[{"email": "a@example.test"}]))
    monkeypatch.setattr(preflight, "MongoClient", FakeClient)
    operation = {"op": "updateValidator", "validator": {"$jsonSchema": {"required": ["email"], "properties": {"email": {"bsonType": "string"}}}}}
    findings, checks = run_preflight(_manifest(tmp_path, operation), _config())
    assert findings == ()
    assert checks[0].status == "sampled"


def test_connection_failure_is_sanitized_and_incomplete(tmp_path, monkeypatch):
    from migration_auditor import preflight

    class FailedClient(FakeClient):
        def __getitem__(self, name):
            raise ServerSelectionTimeoutError("mongodb://user:secret@host/db")

    monkeypatch.setattr(preflight, "MongoClient", FailedClient)
    _, checks = run_preflight(_manifest(tmp_path, {"op": "create_index", "keys": {"a": 1}, "unique": True}), _config())
    assert checks[0].status == "incomplete"
    assert "secret" not in checks[0].explanation


def test_synchronous_client_construction_error_is_sanitized(tmp_path, monkeypatch):
    from migration_auditor import preflight

    class InvalidClient:
        def __init__(self, *args, **kwargs):
            raise ValueError("mongodb://user:secret@bad-uri")

    monkeypatch.setattr(preflight, "MongoClient", InvalidClient)
    _, checks = run_preflight(_manifest(tmp_path, {"op": "create_index", "keys": {"a": 1}, "unique": True}), _config())

    assert checks[0].status == "incomplete"
    assert "secret" not in checks[0].explanation


def test_preflight_uses_no_mutation_api(tmp_path, monkeypatch):
    from migration_auditor import preflight

    collection = FakeCollection()
    FakeClient.database = FakeDatabase(collection)
    monkeypatch.setattr(preflight, "MongoClient", FakeClient)
    run_preflight(_manifest(tmp_path, {"op": "create_index", "keys": {"a": 1}, "unique": True}), _config())
    assert not any(hasattr(collection, name) for name in ("insert_one", "update_one", "delete_one", "create_index", "drop"))


def test_query_timeout_is_incomplete(tmp_path, monkeypatch):
    from migration_auditor import preflight

    class TimeoutCollection(FakeCollection):
        def aggregate(self, pipeline, **kwargs):
            raise ServerSelectionTimeoutError("query deadline")

    FakeClient.database = FakeDatabase(TimeoutCollection())
    monkeypatch.setattr(preflight, "MongoClient", FakeClient)
    _, checks = run_preflight(_manifest(tmp_path, {"op": "create_index", "keys": {"a": 1}, "unique": True}), _config())
    assert checks[-1].status == "incomplete"


def test_permission_failure_is_incomplete(tmp_path, monkeypatch):
    from migration_auditor import preflight

    class DeniedDatabase(FakeDatabase):
        def command(self, command):
            raise OperationFailure("not authorized")

    FakeClient.database = DeniedDatabase(FakeCollection())
    monkeypatch.setattr(preflight, "MongoClient", FakeClient)
    _, checks = run_preflight(_manifest(tmp_path, {"op": "create_index", "keys": {"a": 1}, "unique": True}), _config())
    assert checks[0].status == "incomplete"
    assert "OperationFailure" in checks[0].explanation


def test_validator_scan_limit_is_incomplete(tmp_path, monkeypatch):
    from migration_auditor import preflight

    FakeClient.database = FakeDatabase(FakeCollection(documents=[{"required": 1}, {"required": 2}, {"required": 3}]))
    monkeypatch.setattr(preflight, "MongoClient", FakeClient)
    operation = {"op": "updateValidator", "validator": {"$jsonSchema": {"required": ["required"]}}}
    _, checks = run_preflight(_manifest(tmp_path, operation), _config())
    assert checks[0].status == "incomplete"


@pytest.mark.integration
def test_preflight_with_disposable_mongodb(tmp_path):
    fixture_uri = os.environ.get("MIGRATION_AUDITOR_TEST_MONGODB_FIXTURE_URI")
    target_uri = os.environ.get("MIGRATION_AUDITOR_TEST_MONGODB_TARGET_URI")
    if not fixture_uri or not target_uri:
        pytest.skip(
            "set both MIGRATION_AUDITOR_TEST_MONGODB_FIXTURE_URI and "
            "MIGRATION_AUDITOR_TEST_MONGODB_TARGET_URI for disposable preflight integration"
        )
    database_name = f"migration_auditor_preflight_{uuid4().hex}"
    fixture_client = None
    target_client = None
    try:
        fixture_client = MongoClient(fixture_uri, serverSelectionTimeoutMS=2_000)
        target_client = MongoClient(target_uri, serverSelectionTimeoutMS=2_000)
        fixture_database = fixture_client[database_name]
        fixture_database.users.insert_many([{"email": "duplicate"}, {"email": "duplicate"}])

        # Prove the auditor's target identity cannot write, scoped to this disposable DB.
        with pytest.raises(OperationFailure) as denied:
            target_client[database_name].users.insert_one({"email": "target-write-probe"})
        assert denied.value.code == 13

        files = _manifest(tmp_path, {"op": "create_index", "keys": {"email": 1}, "unique": True})
        config = PreflightConfig(uri=target_uri, database=database_name, max_documents=100)
        findings, _ = run_preflight(files, config)
        assert any(f.rule_id == "MONGO-PREFLIGHT-001" for f in findings)
    finally:
        if target_client is not None:
            target_client.close()
        if fixture_client is not None:
            try:
                fixture_client.drop_database(database_name)
            finally:
                fixture_client.close()
