from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

from fastapi.testclient import TestClient

from migration_auditor.api import MAX_FILE_BYTES, _policy_failed, app
from migration_auditor.config import AuditorConfig, InputConfig, PolicyConfig
from migration_auditor.models import Confidence, Finding, Severity
from migration_auditor.product_store import ProductStore

client = TestClient(app)


def manifest(operations: list[dict[str, object]]) -> bytes:
    return json.dumps({"version": 1, "collection": "users", "operations": operations}).encode()


def test_health() -> None:
    assert client.get("/health").json()["status"] == "ok"


def test_policy_failure_uses_configured_threshold() -> None:
    finding = Finding.create(
        rule_id="MONGO-INDEX-002",
        title="High-write index",
        severity=Severity.MEDIUM,
        confidence=Confidence.HIGH,
        explanation="Test finding.",
        evidence=("test evidence",),
        remediation="Test remediation.",
        documentation_url=None,
        suppressible=True,
    )
    critical_policy = AuditorConfig(
        inputs=InputConfig(migration_paths=(Path("manifest.migration.json"),)),
        policy=PolicyConfig(fail_on=Severity.CRITICAL),
    )

    class Report:
        findings = (finding,)
        has_unanalyzable_input = False

    assert _policy_failed(critical_policy, Report()) is False


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


def test_product_api_scopes_audits_and_rejects_member_admin_access(monkeypatch, tmp_path) -> None:
    import migration_auditor.api as api_module

    monkeypatch.setattr(api_module, "store", ProductStore(str(tmp_path / "product.sqlite3")))
    first_response = client.post("/api/auth/signup", json={"email": "first@example.com", "password": "secure password", "display_name": "First", "organization_name": "First Org"})
    second_response = client.post("/api/auth/signup", json={"email": "second@example.com", "password": "another password", "display_name": "Second", "organization_name": "Second Org"})
    first_headers = {"Cookie": f"auditor_session={first_response.cookies['auditor_session']}"}
    second_headers = {"Cookie": f"auditor_session={second_response.cookies['auditor_session']}"}
    first_org = client.get("/api/me", headers=first_headers).json()["organizations"][0]["id"]
    created = client.post(f"/api/product/audits?organization_id={first_org}", headers=first_headers, files={"files": ("risk.migration.json", manifest([{"op": "drop_collection"}]), "application/json")})

    assert created.status_code == 200
    assert len(client.get("/api/product/audits", headers=first_headers).json()) == 1
    assert client.get("/api/product/audits", headers=second_headers).json() == []
    assert client.get("/api/admin/users", headers=first_headers).status_code == 403


def test_simple_admin_mode_exposes_only_sanitized_in_memory_summary(monkeypatch) -> None:
    monkeypatch.setenv("SIMPLE_ADMIN_MODE", "true")
    response = client.post(
        "/api/audits",
        files={"files": ("risk.migration.json", manifest([{"op": "drop_collection"}]), "application/json")},
    )
    summary = client.get("/api/simple-admin/summary")

    assert response.status_code == 200
    assert summary.status_code == 401
