"""Offline static analysis for the first supported migration-manifest format."""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from migration_auditor.config import AuditorConfig, RuleConfig
from migration_auditor.models import Confidence, Finding, Severity, SourceLocation

MANIFEST_VERSION = 1
SUPPORTED_OPERATIONS = {"create_index", "drop_collection"}


class AnalysisError(ValueError):
    """The input could not be read as a valid supported manifest."""


@dataclass(frozen=True)
class Operation:
    name: str
    collection: str
    payload: Mapping[str, Any]
    location: SourceLocation


@dataclass(frozen=True)
class AnalysisReport:
    files: tuple[Path, ...]
    findings: tuple[Finding, ...]
    errors: tuple[str, ...]

    @property
    def unsupported_count(self) -> int:
        return sum(finding.rule_id == "MONGO-ANALYSIS-001" for finding in self.findings)

    @property
    def successful(self) -> bool:
        return not self.errors

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": "completed" if self.successful else "error",
            "files_analyzed": len(self.files),
            "unsupported_operations_count": self.unsupported_count,
            "findings": [finding.to_dict() for finding in self.findings],
            "errors": list(self.errors),
        }


def discover_manifests(paths: Iterable[Path]) -> tuple[Path, ...]:
    """Discover only `*.migration.json` files; arbitrary code is never considered input."""
    discovered: set[Path] = set()
    for path in paths:
        if path.is_file() and path.name.endswith(".migration.json"):
            discovered.add(path)
        elif path.is_dir():
            discovered.update(candidate for candidate in path.rglob("*.migration.json") if candidate.is_file())
    return tuple(sorted(discovered))


def _source_path(path: Path) -> str:
    try:
        return path.relative_to(Path.cwd()).as_posix()
    except ValueError:
        return path.as_posix()


def parse_manifest(path: Path) -> tuple[Operation, ...]:
    """Parse one declarative JSON manifest without importing or executing any migration code."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise AnalysisError(f"{_source_path(path)}: could not read manifest") from exc
    except json.JSONDecodeError as exc:
        raise AnalysisError(f"{_source_path(path)}: invalid JSON manifest") from exc
    if not isinstance(raw, Mapping):
        raise AnalysisError(f"{_source_path(path)}: manifest root must be an object")
    if set(raw) != {"version", "collection", "operations"}:
        raise AnalysisError(
            f"{_source_path(path)}: manifest requires exactly version, collection, and operations"
        )
    if raw["version"] != MANIFEST_VERSION:
        raise AnalysisError(f"{_source_path(path)}: unsupported manifest version")
    collection = raw["collection"]
    operations = raw["operations"]
    if not isinstance(collection, str) or not collection.strip():
        raise AnalysisError(f"{_source_path(path)}: collection must be a non-empty string")
    if not isinstance(operations, list):
        raise AnalysisError(f"{_source_path(path)}: operations must be an array")

    parsed: list[Operation] = []
    for number, operation in enumerate(operations, start=1):
        location = SourceLocation(file=_source_path(path), operation=f"operations[{number}]")
        if not isinstance(operation, Mapping):
            parsed.append(Operation("__unsupported__", collection, {}, location))
            continue
        name = operation.get("op")
        if not isinstance(name, str):
            parsed.append(Operation("__unsupported__", collection, operation, location))
            continue
        parsed.append(Operation(name, collection, operation, location))
    return tuple(parsed)


def _rule_enabled(rule_id: str, config: RuleConfig) -> bool:
    # Unknown/dynamic syntax must never be silently treated as safe.
    if rule_id == "MONGO-ANALYSIS-001":
        return True
    return rule_id not in config.disabled and (not config.enabled or rule_id in config.enabled)


def _severity(rule_id: str, default: Severity, config: RuleConfig) -> Severity:
    return config.severity_overrides.get(rule_id, default)


def _unsupported(operation: Operation, config: RuleConfig) -> Finding:
    name = operation.name if operation.name != "__unsupported__" else "dynamic or malformed operation"
    return Finding.create(
        rule_id="MONGO-ANALYSIS-001",
        title="Unsupported migration operation",
        severity=_severity("MONGO-ANALYSIS-001", Severity.INFO, config),
        confidence=Confidence.HIGH,
        explanation="This operation is outside the supported declarative manifest subset.",
        evidence=(f"operation: {name}",),
        remediation="Rewrite it using a documented supported operation or review it manually.",
        documentation_url="docs/rules.md#mongo-analysis-001",
        suppressible=False,
        location=operation.location,
        collection=operation.collection,
    )


def analyze_operation(operation: Operation, config: RuleConfig) -> tuple[Finding, ...]:
    """Apply the first deterministic rules to one normalized operation."""
    if operation.name not in SUPPORTED_OPERATIONS:
        return (_unsupported(operation, config),)

    findings: list[Finding] = []
    if operation.name == "create_index":
        if set(operation.payload) - {"op", "keys", "unique", "name"}:
            return (_unsupported(operation, config),)
        unique = operation.payload.get("unique", False)
        keys = operation.payload.get("keys")
        valid_keys = isinstance(keys, Mapping) and bool(keys) and all(
            isinstance(key, str) and value in (1, -1) for key, value in keys.items()
        )
        if not isinstance(unique, bool) or not valid_keys:
            return (_unsupported(operation, config),)
        if unique and _rule_enabled("MONGO-INDEX-001", config):
            name = operation.payload.get("name")
            index_name = name if isinstance(name, str) and name else None
            findings.append(
                Finding.create(
                    rule_id="MONGO-INDEX-001",
                    title="Unique index may fail on existing duplicates",
                    severity=_severity("MONGO-INDEX-001", Severity.HIGH, config),
                    confidence=Confidence.MEDIUM,
                    explanation=(
                        "Static analysis cannot determine whether existing documents contain duplicate "
                        "index keys."
                    ),
                    evidence=("create_index specifies unique: true",),
                    remediation=(
                        "Run a bounded duplicate-key check on staging before creating the unique index."
                    ),
                    documentation_url="docs/rules.md#mongo-index-001",
                    suppressible=True,
                    location=operation.location,
                    collection=operation.collection,
                    index=index_name,
                )
            )
    if operation.name == "drop_collection" and set(operation.payload) != {"op"}:
        return (_unsupported(operation, config),)
    if operation.name == "drop_collection" and _rule_enabled("MONGO-DATA-002", config):
        findings.append(
            Finding.create(
                rule_id="MONGO-DATA-002",
                title="Destructive collection drop",
                severity=_severity("MONGO-DATA-002", Severity.HIGH, config),
                confidence=Confidence.HIGH,
                explanation="Dropping a collection is destructive and can permanently remove data.",
                evidence=("recognized operation: drop_collection",),
                remediation="Confirm a tested backup and recovery plan before deployment.",
                documentation_url="docs/rules.md#mongo-data-002",
                suppressible=True,
                location=operation.location,
                collection=operation.collection,
            )
        )
    return tuple(findings)


def analyze(config: AuditorConfig) -> AnalysisReport:
    """Discover, parse, and statically analyze configured manifests without a database connection."""
    files = discover_manifests(config.inputs.migration_paths)
    findings: list[Finding] = []
    errors: list[str] = []
    for path in files:
        try:
            operations = parse_manifest(path)
        except AnalysisError as exc:
            errors.append(str(exc))
            continue
        for operation in operations:
            findings.extend(analyze_operation(operation, config.rules))
    return AnalysisReport(files=files, findings=tuple(findings), errors=tuple(errors))


RULES = (
    {
        "rule_id": "MONGO-INDEX-001",
        "title": "Unique index may fail on existing duplicates",
        "default_severity": Severity.HIGH.value,
    },
    {
        "rule_id": "MONGO-DATA-002",
        "title": "Destructive collection drop",
        "default_severity": Severity.HIGH.value,
    },
    {
        "rule_id": "MONGO-ANALYSIS-001",
        "title": "Unsupported migration operation",
        "default_severity": Severity.INFO.value,
    },
)
