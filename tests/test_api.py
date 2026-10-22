from __future__ import annotations

import io
import json
import zipfile

from fastapi.testclient import TestClient

from migration_auditor.api import MAX_FILE_BYTES, app

client = TestClient(app)


def manifest(operations: list[dict[str, object]]) -> bytes:
    return json.dumps({"version": 1, "collection": "users", "operations": operations}).encode()


def test_health() -> None:
    assert client.get("/health").json()["status"] == "ok"


def test_audit_returns_real_analyzer_finding() -> None:
    response = client.post(
        "/api/audits",
        files={"files": ("risk.migration.json", manifest([{"op": "drop_collection"}]), "application/json")},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["policy_failed"] is True
    assert body["findings"][0]["rule_id"] == "MONGO-DATA-002"


def test_malformed_manifest_is_reported_as_invalid_input() -> None:
    response = client.post("/api/audits", files={"files": ("bad.migration.json", b"{", "application/json")})
    assert response.status_code == 422
    assert "invalid JSON manifest" in response.json()["errors"][0]


def test_invalid_upload_is_rejected() -> None:
    response = client.post("/api/audits", files={"files": ("script.py", b"print(1)", "text/plain")})
    assert response.status_code == 400


def test_oversized_upload_is_rejected() -> None:
    response = client.post("/api/audits", files={"files": ("large.migration.json", b"x" * (MAX_FILE_BYTES + 1))})
    assert response.status_code == 413


def test_zip_path_traversal_is_rejected() -> None:
    content = io.BytesIO()
    with zipfile.ZipFile(content, "w") as archive:
        archive.writestr("../escape.migration.json", manifest([]))
    response = client.post("/api/audits", files={"files": ("uploads.zip", content.getvalue(), "application/zip")})
    assert response.status_code == 400


def test_zip_manifests_are_analyzed() -> None:
    content = io.BytesIO()
    with zipfile.ZipFile(content, "w") as archive:
        archive.writestr("nested/risk.migration.json", manifest([{"op": "increment", "field": "count", "idempotent": False}]))
    response = client.post("/api/audits", files={"files": ("uploads.zip", content.getvalue(), "application/zip")})
    assert response.status_code == 200
    assert response.json()["findings"][0]["rule_id"] == "MONGO-EXEC-001"


def test_common_mongo_aliases_reach_the_real_rule_engine() -> None:
    payload = manifest([{"op": "createIndex", "keys": {"email": 1}, "options": {"unique": True}}, {"op": "backfill", "field": "normalized_email"}])
    response = client.post("/api/audits", files={"files": ("migration.migration.json", payload, "application/json")})
    assert response.status_code == 200
    assert [item["rule_id"] for item in response.json()["findings"]] == ["MONGO-INDEX-001", "MONGO-DATA-001"]
    assert response.json()["findings"][0]["location"]["file"] == "upload-1-migration.migration.json"
