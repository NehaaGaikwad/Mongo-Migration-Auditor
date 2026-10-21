from datetime import UTC, datetime

import pytest

from migration_auditor.models import (
    AuditRun,
    Confidence,
    Finding,
    RunStatus,
    Severity,
    SourceLocation,
    StoredRun,
)


def make_finding() -> Finding:
    return Finding.create(
        rule_id="MONGO-INDEX-001",
        title="Unique index may fail",
        severity=Severity.HIGH,
        confidence=Confidence.MEDIUM,
        explanation="Existing duplicate values are unknown in static analysis.",
        evidence=("createIndex is configured with unique: true",),
        remediation="Run a bounded duplicate check in staging before deployment.",
        documentation_url="https://example.invalid/rules/MONGO-INDEX-001",
        suppressible=True,
        location=SourceLocation(file="migrations/001.js", line=12, operation="createIndex"),
        collection="users",
        index="email_unique",
    )


def make_run(findings_count: int = 1) -> AuditRun:
    started = datetime(2026, 10, 21, 8, 0, tzinfo=UTC)
    return AuditRun(
        run_id="run-001",
        analyzer_version="0.1.0",
        started_at=started,
        completed_at=datetime(2026, 10, 21, 8, 1, tzinfo=UTC),
        status=RunStatus.COMPLETED,
        config_fingerprint="a" * 64,
        files_analyzed=1,
        findings_count=findings_count,
    )


def test_finding_id_is_deterministic_and_serializable() -> None:
    first = make_finding()
    second = make_finding()

    assert first.finding_id == second.finding_id
    serialized = first.to_dict()
    assert serialized["severity"] == "high"
    assert serialized["location"] == {
        "file": "migrations/001.js",
        "line": 12,
        "column": None,
        "operation": "createIndex",
    }


def test_stored_run_requires_consistent_finding_count() -> None:
    with pytest.raises(ValueError, match="must equal"):
        StoredRun(run=make_run(findings_count=0), findings=(make_finding(),))
