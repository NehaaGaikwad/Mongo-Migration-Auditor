"""Stable, serializable models shared by rules, reporting, and persistence."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from hashlib import sha256
from typing import Any
from uuid import uuid4


class Severity(StrEnum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"


class Confidence(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    UNKNOWN = "unknown"


class RunStatus(StrEnum):
    COMPLETED = "completed"
    FAILED = "failed"
    INCOMPLETE = "incomplete"


def _utc(value: datetime, name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
    return value.astimezone(UTC)


@dataclass(frozen=True)
class SourceLocation:
    file: str
    line: int | None = None
    column: int | None = None
    operation: str | None = None

    def __post_init__(self) -> None:
        if not self.file.strip():
            raise ValueError("source file must not be empty")
        if self.line is not None and self.line < 1:
            raise ValueError("source line must be positive")
        if self.column is not None and self.column < 1:
            raise ValueError("source column must be positive")

    def to_dict(self) -> dict[str, Any]:
        return {
            "file": self.file,
            "line": self.line,
            "column": self.column,
            "operation": self.operation,
        }


@dataclass(frozen=True)
class Finding:
    """A deterministic finding. Evidence must be sanitized by the producing rule."""

    finding_id: str
    rule_id: str
    title: str
    severity: Severity
    confidence: Confidence
    explanation: str
    evidence: tuple[str, ...]
    remediation: str
    documentation_url: str | None
    suppressible: bool
    location: SourceLocation | None = None
    collection: str | None = None
    field: str | None = None
    index: str | None = None

    def __post_init__(self) -> None:
        for name in ("finding_id", "rule_id", "title", "explanation", "remediation"):
            if not getattr(self, name).strip():
                raise ValueError(f"{name} must not be empty")
        if not self.evidence:
            raise ValueError("evidence must contain at least one sanitized statement")

    @classmethod
    def create(
        cls,
        *,
        rule_id: str,
        title: str,
        severity: Severity,
        confidence: Confidence,
        explanation: str,
        evidence: tuple[str, ...],
        remediation: str,
        documentation_url: str | None,
        suppressible: bool,
        location: SourceLocation | None = None,
        collection: str | None = None,
        field: str | None = None,
        index: str | None = None,
    ) -> Finding:
        stable_parts = (
            rule_id,
            location.file if location else "",
            str(location.line if location else ""),
            collection or "",
            field or "",
            index or "",
            "\n".join(evidence),
        )
        finding_id = sha256("\x1f".join(stable_parts).encode("utf-8")).hexdigest()[:24]
        return cls(
            finding_id=finding_id,
            rule_id=rule_id,
            title=title,
            severity=severity,
            confidence=confidence,
            explanation=explanation,
            evidence=evidence,
            remediation=remediation,
            documentation_url=documentation_url,
            suppressible=suppressible,
            location=location,
            collection=collection,
            field=field,
            index=index,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "finding_id": self.finding_id,
            "rule_id": self.rule_id,
            "title": self.title,
            "severity": self.severity.value,
            "confidence": self.confidence.value,
            "explanation": self.explanation,
            "evidence": list(self.evidence),
            "remediation": self.remediation,
            "documentation_url": self.documentation_url,
            "suppressible": self.suppressible,
            "location": self.location.to_dict() if self.location else None,
            "collection": self.collection,
            "field": self.field,
            "index": self.index,
        }


@dataclass(frozen=True)
class AuditRun:
    run_id: str
    analyzer_version: str
    started_at: datetime
    completed_at: datetime
    status: RunStatus
    config_fingerprint: str
    files_analyzed: int
    findings_count: int
    unknown_operations_count: int = 0

    def __post_init__(self) -> None:
        _utc(self.started_at, "started_at")
        _utc(self.completed_at, "completed_at")
        if self.completed_at < self.started_at:
            raise ValueError("completed_at must not be before started_at")
        if (
            not self.run_id.strip()
            or not self.analyzer_version.strip()
            or not self.config_fingerprint.strip()
        ):
            raise ValueError("run_id, analyzer_version, and config_fingerprint must not be empty")
        if any(
            value < 0
            for value in (self.files_analyzed, self.findings_count, self.unknown_operations_count)
        ):
            raise ValueError("run counters must not be negative")

    @classmethod
    def create(
        cls,
        *,
        analyzer_version: str,
        started_at: datetime,
        completed_at: datetime,
        status: RunStatus,
        config_fingerprint: str,
        files_analyzed: int,
        findings_count: int,
        unknown_operations_count: int = 0,
    ) -> AuditRun:
        return cls(
            run_id=str(uuid4()),
            analyzer_version=analyzer_version,
            started_at=started_at,
            completed_at=completed_at,
            status=status,
            config_fingerprint=config_fingerprint,
            files_analyzed=files_analyzed,
            findings_count=findings_count,
            unknown_operations_count=unknown_operations_count,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "analyzer_version": self.analyzer_version,
            "started_at": _utc(self.started_at, "started_at").isoformat(),
            "completed_at": _utc(self.completed_at, "completed_at").isoformat(),
            "status": self.status.value,
            "config_fingerprint": self.config_fingerprint,
            "files_analyzed": self.files_analyzed,
            "findings_count": self.findings_count,
            "unknown_operations_count": self.unknown_operations_count,
        }


@dataclass(frozen=True)
class StoredRun:
    run: AuditRun
    findings: tuple[Finding, ...]

    def __post_init__(self) -> None:
        if self.run.findings_count != len(self.findings):
            raise ValueError("run.findings_count must equal the number of persisted findings")
