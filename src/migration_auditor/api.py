"""HTTP interface for the offline declarative migration auditor.

Uploads are kept only in a private temporary directory for the duration of a
request.  This module deliberately invokes the package API, never uploaded
code or a shell command.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
import zipfile
from collections.abc import Iterable
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit

from fastapi import Cookie, FastAPI, File, Header, HTTPException, Response, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from migration_auditor import __version__
from migration_auditor.analysis import analyze
from migration_auditor.config import AuditorConfig, InputConfig
from migration_auditor.models import Severity
from migration_auditor.product_store import ProductStore

MAX_FILES = 10
MAX_FILE_BYTES = 1_000_000
MAX_ARCHIVE_BYTES = 5_000_000
MAX_UNPACKED_BYTES = 10_000_000
_SEVERITY_ORDER = {
    Severity.INFO: 0,
    Severity.LOW: 1,
    Severity.MEDIUM: 2,
    Severity.HIGH: 3,
    Severity.CRITICAL: 4,
}


def _origins() -> list[str]:
    """Read and normalize explicit browser origins for credentialed CORS."""
    configured = os.getenv("ALLOWED_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173")
    origins: list[str] = []
    for configured_origin in configured.split(","):
        candidate = configured_origin.strip().rstrip("/")
        if not candidate:
            continue
        parsed = urlsplit(candidate)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.netloc
            or parsed.path
            or parsed.query
            or parsed.fragment
            or parsed.username
            or parsed.password
        ):
            raise ValueError("ALLOWED_ORIGINS must contain comma-separated http(s) origins only")
        origin = f"{parsed.scheme}://{parsed.netloc}"
        if origin not in origins:
            origins.append(origin)
    return origins


def _origin_regex() -> str | None:
    """Read an optional, administrator-controlled origin pattern for preview deployments."""
    configured = os.getenv("ALLOWED_ORIGIN_REGEX", "").strip()
    if not configured:
        return None
    try:
        re.compile(configured)
    except re.error as error:
        raise ValueError("ALLOWED_ORIGIN_REGEX must be a valid regular expression") from error
    return configured


app = FastAPI(title="Mongo Migration Auditor API", version=__version__)
store = ProductStore()
app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins(),
    allow_origin_regex=_origin_regex(),
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type", "Authorization"],
    allow_credentials=True,
)


def _error(status_code: int, detail: str) -> None:
    raise HTTPException(status_code=status_code, detail=detail)


def _current_user(authorization: str | None, session: str | None = None) -> dict[str, object]:
    token = session or (authorization[7:] if authorization and authorization.startswith("Bearer ") else None)
    if not token:
        _error(401, "Authentication required")
    user = store.user_for_token(token)
    if not user:
        _error(401, "Invalid or expired session")
    return user


def _admin(authorization: str | None, session: str | None = None) -> dict[str, object]:
    user = _current_user(authorization, session)
    if user["role"] != "admin":
        store.event(str(user["id"]), "admin_access", "admin_api", "denied")
        _error(403, "Platform admin access required")
    return user


def _demo_mode() -> bool:
    """Enable the intentionally limited, local-only admin summary explicitly."""
    return os.getenv("SIMPLE_ADMIN_MODE", "false").lower() == "true"


def _record_demo_run(report: dict[str, object]) -> None:
    context = store.simple_context()
    if context:
        user, organization = context
        store.save_audit(str(organization["id"]), str(user["id"]), report, __version__)


def _policy_failed(config: AuditorConfig, report: object) -> bool:
    """Apply the configured threshold rather than treating any finding as a failure."""
    return report.has_unanalyzable_input or any(
        _SEVERITY_ORDER[Severity(finding.severity)] >= _SEVERITY_ORDER[config.policy.fail_on]
        for finding in report.findings
    )


def _safe_member(name: str) -> PurePosixPath:
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts or not name.endswith(".migration.json"):
        _error(400, "ZIP may contain only relative .migration.json files")
    if any(part in {"", "."} for part in path.parts):
        _error(400, "ZIP contains an invalid path")
    return path


def _extract_zip(source: Path, destination: Path) -> list[Path]:
    try:
        with zipfile.ZipFile(source) as archive:
            members = [entry for entry in archive.infolist() if not entry.is_dir()]
            if not members:
                _error(400, "ZIP contains no migration manifests")
            if len(members) > MAX_FILES:
                _error(413, f"ZIP may contain at most {MAX_FILES} manifests")
            if sum(entry.file_size for entry in members) > MAX_UNPACKED_BYTES:
                _error(413, "ZIP expands beyond the allowed size")
            output: list[Path] = []
            for entry in members:
                safe_name = _safe_member(entry.filename)
                if entry.file_size > MAX_FILE_BYTES:
                    _error(413, "A manifest exceeds the allowed size")
                target = destination / safe_name.name
                if target.exists():
                    _error(400, "ZIP contains duplicate manifest names")
                with archive.open(entry) as input_file, target.open("xb") as output_file:
                    shutil.copyfileobj(input_file, output_file, length=64 * 1024)
                output.append(target)
            return output
    except zipfile.BadZipFile:
        _error(400, "Uploaded ZIP is invalid")
    return []  # Satisfy static type checkers; _error always raises.


async def _save_uploads(files: Iterable[UploadFile], destination: Path) -> list[Path]:
    uploaded = list(files)
    if not uploaded:
        _error(400, "Upload at least one .migration.json file or one ZIP archive")
    if len(uploaded) > MAX_FILES:
        _error(413, f"Upload at most {MAX_FILES} files")
    paths: list[Path] = []
    for number, upload in enumerate(uploaded, start=1):
        filename = upload.filename or ""
        name = Path(filename).name
        if "/" in filename or "\\" in filename or not (name.endswith(".migration.json") or name.endswith(".zip")):
            _error(400, "Only .migration.json files and .zip archives are accepted")
        limit = MAX_ARCHIVE_BYTES if name.endswith(".zip") else MAX_FILE_BYTES
        target = destination / f"upload-{number}-{name}"
        size = 0
        with target.open("xb") as output:
            while chunk := await upload.read(64 * 1024):
                size += len(chunk)
                if size > limit:
                    _error(413, f"{name} exceeds the allowed size")
                output.write(chunk)
        if name.endswith(".zip"):
            paths.extend(_extract_zip(target, destination))
        else:
            paths.append(target)
    if len(paths) > MAX_FILES:
        _error(413, f"Upload may contain at most {MAX_FILES} manifests")
    return paths


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "version": __version__}


@app.get("/api/simple-admin/summary")
def simple_admin_summary(
    authorization: str | None = Header(default=None),
    auditor_session: str | None = Cookie(default=None),
) -> dict[str, object]:
    """Local dashboard data, available only to an authenticated platform admin."""
    if not _demo_mode():
        _error(404, "Simple admin mode is disabled")
    _admin(authorization, auditor_session)
    runs = store.simple_runs()
    return {
        "mode": "simple_local",
        "audits": len(runs),
        "execution_failures": sum(run["status"] != "completed" for run in runs),
        "policy_failures": sum(run["policy_status"] == "failed" for run in runs),
        "runs": runs,
    }


def _session_response(result: dict[str, object], response: Response) -> dict[str, object]:
    secure = os.getenv("SESSION_COOKIE_SECURE", "false").lower() == "true"
    response.set_cookie("auditor_session", str(result["token"]), httponly=True, secure=secure,
                        samesite="none" if secure else "lax", max_age=7 * 24 * 60 * 60, path="/")
    return {"user": result["user"]}


@app.post("/api/auth/signup")
def signup(payload: dict[str, str], response: Response) -> dict[str, object]:
    email, password = payload.get("email", "").strip(), payload.get("password", "")
    name, organization = payload.get("display_name", "").strip(), payload.get("organization_name", "").strip()
    if not email or "@" not in email or len(password) < 12 or not name or not organization:
        _error(422, "Email, display name, organization, and a password of at least 12 characters are required")
    try:
        result = store.signup(email, password, name, organization)
    except ValueError as error:
        _error(409, str(error))
    store.event(str(result["user"]["id"]), "signup", "account", "success")
    return _session_response(result, response)


@app.post("/api/auth/login")
def login(payload: dict[str, str], response: Response) -> dict[str, object]:
    try:
        result = store.login(payload.get("email", ""), payload.get("password", ""))
    except ValueError as error:
        _error(401, str(error))
    store.event(str(result["user"]["id"]), "login", "session", "success")
    return _session_response(result, response)


@app.post("/api/auth/logout")
def logout(response: Response, authorization: str | None = Header(default=None), auditor_session: str | None = Cookie(default=None)) -> dict[str, str]:
    user = _current_user(authorization, auditor_session)
    token = auditor_session or authorization[7:]
    store.end_session(token)
    response.delete_cookie("auditor_session", path="/")
    store.event(str(user["id"]), "logout", "session", "success")
    return {"status": "ok"}


@app.get("/api/me")
def me(authorization: str | None = Header(default=None), auditor_session: str | None = Cookie(default=None)) -> dict[str, object]:
    user = _current_user(authorization, auditor_session)
    return {"user": user, "organizations": store.organizations_for(str(user["id"]))}


@app.get("/api/product/audits")
def product_audits(authorization: str | None = Header(default=None), auditor_session: str | None = Cookie(default=None)) -> list[dict[str, object]]:
    return store.audits_for(_current_user(authorization, auditor_session))


@app.get("/api/admin/users")
def admin_users(authorization: str | None = Header(default=None), auditor_session: str | None = Cookie(default=None)) -> list[dict[str, object]]:
    admin = _admin(authorization, auditor_session)
    store.event(str(admin["id"]), "list_users", "users", "success")
    return store.admin_users()


@app.get("/api/admin/organizations")
def admin_organizations(authorization: str | None = Header(default=None), auditor_session: str | None = Cookie(default=None)) -> list[dict[str, object]]:
    admin = _admin(authorization, auditor_session)
    store.event(str(admin["id"]), "list_organizations", "organizations", "success")
    return store.admin_orgs()


@app.get("/api/admin/audits")
def admin_audits(authorization: str | None = Header(default=None), auditor_session: str | None = Cookie(default=None)) -> list[dict[str, object]]:
    admin = _admin(authorization, auditor_session)
    store.event(str(admin["id"]), "list_audits", "audit_runs", "success")
    return store.audits_for(admin, all_tenants=True)


@app.get("/api/admin/security-events")
def admin_security_events(authorization: str | None = Header(default=None), auditor_session: str | None = Cookie(default=None)) -> list[dict[str, object]]:
    admin = _admin(authorization, auditor_session)
    store.event(str(admin["id"]), "list_security_events", "security_events", "success")
    return store.security_events()


@app.get("/api/admin/metrics")
def admin_metrics(authorization: str | None = Header(default=None), auditor_session: str | None = Cookie(default=None)) -> dict[str, int]:
    admin = _admin(authorization, auditor_session)
    store.event(str(admin["id"]), "read_metrics", "platform_metrics", "success")
    return store.metrics()


@app.post("/api/audits")
async def audit(files: list[UploadFile] = File(...)) -> dict[str, object]:
    """Analyze uploaded declarative manifests and return the package report."""
    with tempfile.TemporaryDirectory(prefix="mongo-migration-auditor-") as temporary:
        paths = await _save_uploads(files, Path(temporary))
        config = AuditorConfig(inputs=InputConfig(migration_paths=tuple(paths)))
        report = analyze(config)
    response = report.to_dict()
    # Temporary server paths are not useful to a browser client and must not be
    # exposed. Uploaded names are sufficient to identify the affected manifest.
    for finding in response["findings"]:
        location = finding.get("location")
        if location:
            location["file"] = Path(location["file"]).name
    response["errors"] = [
        error.replace(Path(temporary).as_posix(), "uploaded") for error in response["errors"]
    ]
    response["policy_failed"] = _policy_failed(config, report)
    response["status"] = "invalid_input" if report.errors else (
        "policy_failed" if response["policy_failed"] else "completed"
    )
    if _demo_mode():
        _record_demo_run(response)
    if report.errors:
        # The client still receives the real partial report and clear input errors.
        return JSONResponse(status_code=422, content=response)
    return response


@app.post("/api/product/audits")
async def create_product_audit(
    organization_id: str,
    files: list[UploadFile] = File(...),
    authorization: str | None = Header(default=None),
    auditor_session: str | None = Cookie(default=None),
) -> dict[str, object]:
    """Persist a tenant-authorized audit summary without retaining raw uploads."""
    user = _current_user(authorization, auditor_session)
    if not store.owns_org(str(user["id"]), organization_id):
        _error(403, "You do not have access to this organization")
    result = await audit(files)
    if isinstance(result, JSONResponse):
        report = json.loads(result.body)
    else:
        report = result
    run_id = store.save_audit(organization_id, str(user["id"]), report, __version__)
    store.event(str(user["id"]), "create_audit", run_id, "success")
    return {"run_id": run_id, "report": report}
