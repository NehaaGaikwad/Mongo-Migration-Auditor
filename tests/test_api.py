from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.testclient import TestClient

from migration_auditor.api import MAX_FILE_BYTES, _origin_regex, _origins, _policy_failed, app
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


def test_cors_accepts_both_vercel_origins_and_returns_headers_on_unauthorized_error(monkeypatch) -> None:
    primary = "https://mongo-migration-auditor-frontend.vercel.app"
    secondary = "https://mongo-migration-auditor-frontend-6xpdebs9h.vercel.app"
    monkeypatch.setenv("ALLOWED_ORIGINS", f" {primary}/, {secondary} ")
    cors_app = FastAPI()
    cors_app.add_middleware(
        CORSMiddleware,
        allow_origins=_origins(),
        allow_origin_regex=_origin_regex(),
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type", "Authorization"],
        allow_credentials=True,
    )

    @cors_app.post("/api/auth/login")
    def cors_login() -> None:
        return None

    @cors_app.get("/api/me")
    def cors_me() -> None:
        from fastapi import HTTPException

        raise HTTPException(status_code=401, detail="Authentication required")

    cors_client = TestClient(cors_app)
    for origin in (primary, secondary):
        preflight = cors_client.options(
            "/api/auth/login",
            headers={
                "Origin": origin,
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "content-type",
            },
        )
        error = cors_client.get("/api/me", headers={"Origin": origin})
        assert preflight.status_code == 200
        assert preflight.headers["access-control-allow-origin"] == origin
        assert preflight.headers["access-control-allow-credentials"] == "true"
        assert error.status_code == 401
        assert error.headers["access-control-allow-origin"] == origin

    rejected = cors_client.options(
        "/api/auth/login",
        headers={
            "Origin": "https://untrusted.example",
            "Access-Control-Request-Method": "POST",
        },
    )
    assert rejected.status_code == 400
    assert "access-control-allow-origin" not in rejected.headers


def test_origins_reject_malformed_values(monkeypatch) -> None:
    monkeypatch.setenv("ALLOWED_ORIGINS", "https://example.com/not-an-origin")
    with pytest.raises(ValueError, match="ALLOWED_ORIGINS"):
        _origins()


def test_preview_origin_regex_is_opt_in_and_project_scoped(monkeypatch) -> None:
    monkeypatch.setenv(
        "ALLOWED_ORIGIN_REGEX",
        r"^https://mongo-migration-auditor-frontend(?:-[a-z0-9-]+)?\.vercel\.app$",
    )
    cors_app = FastAPI()
    cors_app.add_middleware(
        CORSMiddleware,
        allow_origins=[],
        allow_origin_regex=_origin_regex(),
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type"],
        allow_credentials=True,
    )

    @cors_app.post("/api/auth/login")
    def preview_login() -> None:
        return None

    cors_client = TestClient(cors_app)
    allowed = cors_client.options(
        "/api/auth/login",
        headers={
            "Origin": "https://mongo-migration-auditor-frontend-g5hbbb.vercel.app",
            "Access-Control-Request-Method": "POST",
        },
    )
    rejected = cors_client.options(
        "/api/auth/login",
        headers={
            "Origin": "https://mongo-migration-auditor-frontend-evil.example",
            "Access-Control-Request-Method": "POST",
        },
    )

    assert allowed.status_code == 200
    assert allowed.headers["access-control-allow-origin"] == "https://mongo-migration-auditor-frontend-g5hbbb.vercel.app"
    assert rejected.status_code == 400


def test_login_session_cookie_is_secure_and_continues_to_me(monkeypatch, tmp_path) -> None:
    import migration_auditor.api as api_module

    monkeypatch.setattr(api_module, "store", ProductStore(str(tmp_path / "product.sqlite3")))
    monkeypatch.setenv("SESSION_COOKIE_SECURE", "true")
    isolated_client = TestClient(app, base_url="https://testserver")
    signup_response = isolated_client.post(
        "/api/auth/signup",
        json={
            "email": "member@example.com",
            "password": "a sufficiently long password",
            "display_name": "Member",
            "organization_name": "Member Org",
        },
    )

    assert signup_response.status_code == 200
    assert "HttpOnly" in signup_response.headers["set-cookie"]
    assert "SameSite=none" in signup_response.headers["set-cookie"]
    assert "Secure" in signup_response.headers["set-cookie"]
    assert isolated_client.get("/api/me").json()["user"]["email"] == "member@example.com"
