"""HTTP interface for the offline declarative migration auditor.

Uploads are kept only in a private temporary directory for the duration of a
request.  This module deliberately invokes the package API, never uploaded
code or a shell command.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import zipfile
from collections.abc import Iterable
from pathlib import Path, PurePosixPath

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from migration_auditor import __version__
from migration_auditor.analysis import analyze
from migration_auditor.config import AuditorConfig, InputConfig

MAX_FILES = 10
MAX_FILE_BYTES = 1_000_000
MAX_ARCHIVE_BYTES = 5_000_000
MAX_UNPACKED_BYTES = 10_000_000


def _origins() -> list[str]:
    """Read explicit browser origins; localhost is useful for local development."""
    configured = os.getenv("ALLOWED_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173")
    return [origin.strip() for origin in configured.split(",") if origin.strip()]


app = FastAPI(title="Mongo Migration Auditor API", version=__version__)
app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins(),
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)


def _error(status_code: int, detail: str) -> None:
    raise HTTPException(status_code=status_code, detail=detail)


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


@app.post("/api/audits")
async def audit(files: list[UploadFile] = File(...)) -> dict[str, object]:
    """Analyze uploaded declarative manifests and return the package report."""
    with tempfile.TemporaryDirectory(prefix="mongo-migration-auditor-") as temporary:
        paths = await _save_uploads(files, Path(temporary))
        report = analyze(AuditorConfig(inputs=InputConfig(migration_paths=tuple(paths))))
    response = report.to_dict()
    response["policy_failed"] = any(
        finding.severity.value in {"high", "critical"} for finding in report.findings
    )
    response["status"] = "invalid_input" if report.errors else (
        "policy_failed" if response["policy_failed"] else "completed"
    )
    if report.errors:
        # The client still receives the real partial report and clear input errors.
        return JSONResponse(status_code=422, content=response)
    return response
