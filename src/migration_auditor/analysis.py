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
SUPPORTED_OPERATIONS = {
    "add_field",
    "backfill",
    "bulk_update",
    "change_field_type",
    "create_collection",
    "create_index",
    "delete_many",
    "drop_index",
    "drop_collection",
    "increment",
    "remove_field",
    "rename_field",
    "update_validator",
}

# The JSON adapter accepts these spelling variants only.  It deliberately does
# not attempt to infer semantics from arbitrary driver or application code.
OPERATION_ALIASES = {
    "addField": "add_field",
    "add_field": "add_field",
    "backfill": "backfill",
    "bulkUpdate": "bulk_update",
    "bulk_update": "bulk_update",
    "changeFieldType": "change_field_type",
    "change_field_type": "change_field_type",
    "createCollection": "create_collection",
    "create_collection": "create_collection",
    "createIndex": "create_index",
    "create_index": "create_index",
    "deleteMany": "delete_many",
    "delete_many": "delete_many",
    "dropCollection": "drop_collection",
    "drop_collection": "drop_collection",
    "dropIndex": "drop_index",
    "drop_index": "drop_index",
    "increment": "increment",
    "removeField": "remove_field",
    "remove_field": "remove_field",
    "renameField": "rename_field",
    "rename_field": "rename_field",
    "updateMany": "bulk_update",
    "updateValidator": "update_validator",
    "update_validator": "update_validator",
}


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


def _normalise_operation(raw: Mapping[str, Any]) -> tuple[str, Mapping[str, Any]]:
    """Translate documented Mongo-style JSON aliases to the canonical model.

    A failed translation intentionally remains an unsupported operation. This
    keeps one rule engine and prevents unrecognised fields from looking safe.
    """
    op_name = raw.get("op")
    type_name = raw.get("type")
    if op_name is not None and not isinstance(op_name, str):
        return "__unsupported__", raw
    if type_name is not None and not isinstance(type_name, str):
        return "__unsupported__", raw
    if op_name is None and type_name is None:
        return "__unsupported__", raw
    op_canonical = OPERATION_ALIASES.get(op_name) if isinstance(op_name, str) else None
    type_canonical = OPERATION_ALIASES.get(type_name) if isinstance(type_name, str) else None
    # Different spellings are allowed only when they describe the same known
    # canonical operation. Conflicting operation declarations must fail closed.
    if op_name is not None and type_name is not None and op_canonical != type_canonical:
        return "__unsupported__", raw
    canonical = type_canonical if type_name is not None else op_canonical
    if canonical is None:
        return "__unsupported__", raw
    payload = dict(raw)
    payload["op"] = canonical
    payload.pop("type", None)
    aliases = {
        "buildContext": "build_context",
        "compatibilityWindow": "compatibility_window",
        "conversionPlan": "conversion_plan",
        "filterScope": "filter_scope",
        "recoveryPlan": "recovery_plan",
    }
    for source, target in aliases.items():
        if source in payload:
            if target in payload:
                return "__unsupported__", raw
            payload[target] = payload.pop(source)

    if canonical == "create_index" and "options" in payload:
        options = payload.pop("options")
        if not isinstance(options, Mapping):
            return "__unsupported__", raw
        allowed = {"unique", "name", "build_context", "buildContext"}
        if set(options) - allowed:
            return "__unsupported__", raw
        for key, value in options.items():
            target = aliases.get(key, key)
            if target in payload:
                return "__unsupported__", raw
            payload[target] = value
    if canonical == "create_index" and "write_rate" in payload:
        write_rate = payload.pop("write_rate")
        # The canonical model only represents the high-write condition that
        # MONGO-INDEX-002 can assess. Other labels are not silently downgraded.
        if write_rate != "high" or (
            "build_context" in payload and payload["build_context"] != "high_write"
        ):
            return "__unsupported__", raw
        payload["build_context"] = "high_write"

    if canonical == "update_validator" and "validator" in payload:
        validator = payload.pop("validator")
        if "added_required" in payload or not isinstance(validator, Mapping):
            return "__unsupported__", raw
        schema = validator.get("$jsonSchema")
        if set(validator) != {"$jsonSchema"} or not isinstance(schema, Mapping):
            return "__unsupported__", raw
        required = schema.get("required", [])
        if set(schema) - {"bsonType", "required", "properties"}:
            return "__unsupported__", raw
        payload["added_required"] = required

    if canonical == "delete_many" and "filter" in payload:
        filter_value = payload.pop("filter")
        if not isinstance(filter_value, Mapping) or "filter_scope" in payload:
            return "__unsupported__", raw
        payload["filter_scope"] = "all" if not filter_value else "filtered"
    if canonical == "delete_many":
        payload.setdefault("recovery_plan", False)
    if canonical in {"backfill", "bulk_update"} and "options" in payload:
        options = payload.pop("options")
        if not isinstance(options, Mapping) or set(options) - {"batchSize", "checkpoint", "throttleMs"}:
            return "__unsupported__", raw
        for key, value in options.items():
            if key in payload:
                return "__unsupported__", raw
            payload[key] = value
    if canonical in {"backfill", "bulk_update"}:
        # Numeric batch/throttle declarations are normalized to the existing
        # boolean safeguards; their values are not execution instructions.
        for source, target in (("batchSize", "batched"), ("checkpoint", "checkpointed"), ("throttleMs", "throttled")):
            if source in payload:
                if target in payload:
                    return "__unsupported__", raw
                value = payload.pop(source)
                if source in {"batchSize", "throttleMs"}:
                    if type(value) is not int or value <= 0:
                        return "__unsupported__", raw
                    payload[target] = True
                else:
                    payload[target] = value
        for safeguard in ("batched", "checkpointed", "throttled"):
            payload.setdefault(safeguard, False)
    if canonical == "bulk_update":
        update = payload.pop("update", None)
        filter_value = payload.pop("filter", None)
        if (
            not isinstance(filter_value, Mapping)
            or not isinstance(update, Mapping)
            or set(update) - {"$set", "$unset"}
            or not update
            or not all(isinstance(value, Mapping) and value for value in update.values())
        ):
            return "__unsupported__", raw
        payload["filter_scope"] = "all" if not filter_value else "filtered"
    return canonical, payload


def parse_manifest(path: Path) -> tuple[Operation, ...]:
    """Parse one declarative JSON manifest without importing or executing any migration code."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise AnalysisError(f"{_source_path(path)}: could not read manifest") from exc
    except UnicodeDecodeError as exc:
        raise AnalysisError(f"{_source_path(path)}: manifest is not valid UTF-8") from exc
    except json.JSONDecodeError as exc:
        raise AnalysisError(f"{_source_path(path)}: invalid JSON manifest") from exc
    if not isinstance(raw, Mapping):
        raise AnalysisError(f"{_source_path(path)}: manifest root must be an object")
    if set(raw) != {"version", "collection", "operations"}:
        raise AnalysisError(
            f"{_source_path(path)}: manifest requires exactly version, collection, and operations"
        )
    if type(raw["version"]) is not int or raw["version"] != MANIFEST_VERSION:
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
        name, payload = _normalise_operation(operation)
        declared_name = operation.get("op", operation.get("type"))
        if name == "__unsupported__" and isinstance(declared_name, str):
            name = f"unsupported declaration: {declared_name}"
        parsed.append(Operation(name, collection, payload, location))
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


def _finding(
    operation: Operation,
    config: RuleConfig,
    *,
    rule_id: str,
    title: str,
    default_severity: Severity,
    confidence: Confidence,
    explanation: str,
    evidence: tuple[str, ...],
    remediation: str,
    field: str | None = None,
    index: str | None = None,
) -> Finding:
    """Create a consistently configured finding for a supported operation."""
    return Finding.create(
        rule_id=rule_id,
        title=title,
        severity=_severity(rule_id, default_severity, config),
        confidence=confidence,
        explanation=explanation,
        evidence=evidence,
        remediation=remediation,
        documentation_url=f"docs/rules.md#{rule_id.lower()}",
        suppressible=True,
        location=operation.location,
        collection=operation.collection,
        field=field,
        index=index,
    )


def _field_name(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def analyze_operation(operation: Operation, config: RuleConfig) -> tuple[Finding, ...]:
    """Apply deterministic rules to one normalized declarative operation."""
    if operation.name not in SUPPORTED_OPERATIONS:
        return (_unsupported(operation, config),)

    findings: list[Finding] = []
    if operation.name == "create_index":
        if set(operation.payload) - {"op", "keys", "unique", "name", "build_context"}:
            return (_unsupported(operation, config),)
        unique = operation.payload.get("unique", False)
        keys = operation.payload.get("keys")
        valid_keys = isinstance(keys, Mapping) and bool(keys) and all(
            isinstance(key, str)
            and bool(key.strip())
            and type(value) is int
            and value in (1, -1)
            for key, value in keys.items()
        )
        name = operation.payload.get("name")
        valid_name = "name" not in operation.payload or (
            isinstance(name, str) and bool(name.strip())
        )
        build_context = operation.payload.get("build_context", "unknown")
        if (
            not isinstance(unique, bool)
            or not valid_keys
            or not valid_name
            or build_context not in {"unknown", "high_write"}
        ):
            return (_unsupported(operation, config),)
        if unique and _rule_enabled("MONGO-INDEX-001", config):
            findings.append(
                _finding(
                    operation,
                    config,
                    rule_id="MONGO-INDEX-001",
                    title="Unique index may fail on existing duplicates",
                    default_severity=Severity.HIGH,
                    confidence=Confidence.MEDIUM,
                    explanation=(
                        "Static analysis cannot determine whether existing documents contain duplicate "
                        "index keys."
                    ),
                    evidence=("create_index specifies unique: true",),
                    remediation=(
                        "Run a bounded duplicate-key check on staging before creating the unique index."
                    ),
                    index=name,
                )
            )
        if build_context == "high_write" and _rule_enabled("MONGO-INDEX-002", config):
            findings.append(
                _finding(
                    operation,
                    config,
                    rule_id="MONGO-INDEX-002",
                    title="Index build declared for a high-write collection",
                    default_severity=Severity.MEDIUM,
                    confidence=Confidence.MEDIUM,
                    explanation="An index build can add operational pressure while a collection is receiving high write traffic.",
                    evidence=("create_index specifies build_context: high_write",),
                    remediation="Validate the index-build plan and capacity on staging, then schedule an appropriate deployment window.",
                    index=name,
                )
            )
    elif operation.name == "drop_collection":
        if set(operation.payload) != {"op"}:
            return (_unsupported(operation, config),)
        if _rule_enabled("MONGO-DATA-002", config):
            findings.append(
                _finding(
                    operation,
                    config,
                    rule_id="MONGO-DATA-002",
                    title="Destructive collection operation",
                    default_severity=Severity.HIGH,
                    confidence=Confidence.HIGH,
                    explanation="Dropping a collection is destructive and can permanently remove data.",
                    evidence=("recognized operation: drop_collection",),
                    remediation="Confirm a tested backup and recovery plan before deployment.",
                )
            )
    elif operation.name == "create_collection":
        if set(operation.payload) != {"op"}:
            return (_unsupported(operation, config),)
    elif operation.name == "drop_index":
        if set(operation.payload) != {"op", "name"} or not _field_name(operation.payload.get("name")):
            return (_unsupported(operation, config),)
        if _rule_enabled("MONGO-INDEX-003", config):
            findings.append(
                _finding(
                    operation, config, rule_id="MONGO-INDEX-003", title="Index removal may affect query performance",
                    default_severity=Severity.MEDIUM, confidence=Confidence.MEDIUM,
                    explanation="Removing an index can change query plans and increase load for dependent queries.",
                    evidence=(f"drop_index specifies name: {operation.payload['name']}",),
                    remediation="Review query plans and rollout capacity before removing the index.",
                    index=operation.payload["name"],
                )
            )
    elif operation.name == "delete_many":
        if set(operation.payload) != {"op", "filter_scope", "recovery_plan"}:
            return (_unsupported(operation, config),)
        scope = operation.payload.get("filter_scope")
        recovery_plan = operation.payload.get("recovery_plan")
        if scope not in {"all", "filtered"} or not isinstance(recovery_plan, bool):
            return (_unsupported(operation, config),)
        if _rule_enabled("MONGO-DATA-002", config):
            findings.append(
                _finding(
                    operation,
                    config,
                rule_id="MONGO-DATA-002",
                    title="Destructive multi-document delete",
                    default_severity=Severity.HIGH,
                    confidence=Confidence.HIGH,
                    explanation="Deleting many documents is destructive even when the declared scope is filtered.",
                    evidence=(f"delete_many specifies filter_scope: {scope}",),
                    remediation="Confirm a tested backup and recovery plan before deployment.",
                )
            )
    elif operation.name == "update_validator":
        if set(operation.payload) != {"op", "added_required"}:
            return (_unsupported(operation, config),)
        fields = operation.payload.get("added_required")
        if not isinstance(fields, list) or not all(_field_name(field) for field in fields):
            return (_unsupported(operation, config),)
        if len(set(fields)) != len(fields):
            return (_unsupported(operation, config),)
        if fields and _rule_enabled("MONGO-SCHEMA-001", config):
            for field in fields:
                findings.append(
                    _finding(
                        operation, config, rule_id="MONGO-SCHEMA-001",
                        title="Validator adds a required field", default_severity=Severity.HIGH,
                        confidence=Confidence.HIGH,
                        explanation="Existing documents may not contain a newly required field.",
                        evidence=(f"update_validator adds required field: {field}",),
                        remediation="Backfill and validate existing documents before enforcing the requirement.",
                        field=field,
                    )
                )
    elif operation.name == "add_field":
        if set(operation.payload) != {"op", "field", "required"} or not _field_name(operation.payload.get("field")) or not isinstance(operation.payload.get("required"), bool):
            return (_unsupported(operation, config),)
        if operation.payload["required"] and _rule_enabled("MONGO-SCHEMA-001", config):
            findings.append(_finding(operation, config, rule_id="MONGO-SCHEMA-001", title="Added field is immediately required", default_severity=Severity.HIGH, confidence=Confidence.HIGH, explanation="Existing documents may not contain the newly required field.", evidence=(f"add_field specifies required field: {operation.payload['field']}",), remediation="Backfill and validate existing documents before enforcing the requirement.", field=operation.payload["field"]))
    elif operation.name == "remove_field":
        if set(operation.payload) != {"op", "field", "compatibility_window"}:
            return (_unsupported(operation, config),)
        field, compatible = operation.payload.get("field"), operation.payload.get("compatibility_window")
        if not _field_name(field) or not isinstance(compatible, bool):
            return (_unsupported(operation, config),)
        if not compatible and _rule_enabled("MONGO-SCHEMA-002", config):
            findings.append(_finding(operation, config, rule_id="MONGO-SCHEMA-002", title="Field removal lacks a compatibility window", default_severity=Severity.HIGH, confidence=Confidence.HIGH, explanation="Older application versions may still read or write the removed field.", evidence=(f"remove_field specifies field: {field}",), remediation="Deploy a compatibility window and remove the field only after dependent versions are retired.", field=field))
    elif operation.name == "rename_field":
        if set(operation.payload) != {"op", "from", "to", "compatibility_window"}:
            return (_unsupported(operation, config),)
        source, target, compatible = operation.payload.get("from"), operation.payload.get("to"), operation.payload.get("compatibility_window")
        if not _field_name(source) or not _field_name(target) or source == target or not isinstance(compatible, bool):
            return (_unsupported(operation, config),)
        if not compatible and _rule_enabled("MONGO-SCHEMA-002", config):
            findings.append(_finding(operation, config, rule_id="MONGO-SCHEMA-002", title="Field rename lacks a compatibility window", default_severity=Severity.HIGH, confidence=Confidence.HIGH, explanation="Older application versions may still use the original field name.", evidence=(f"rename_field specifies {source} -> {target}",), remediation="Dual-read or dual-write during a compatibility window before retiring the original field.", field=source))
    elif operation.name == "change_field_type":
        if set(operation.payload) != {"op", "field", "from_type", "to_type", "conversion_plan"}:
            return (_unsupported(operation, config),)
        field, old, new, planned = (operation.payload.get(key) for key in ("field", "from_type", "to_type", "conversion_plan"))
        if not _field_name(field) or not _field_name(old) or not _field_name(new) or old == new or not isinstance(planned, bool):
            return (_unsupported(operation, config),)
        if not planned and _rule_enabled("MONGO-SCHEMA-003", config):
            findings.append(_finding(operation, config, rule_id="MONGO-SCHEMA-003", title="Field type change lacks a conversion plan", default_severity=Severity.HIGH, confidence=Confidence.HIGH, explanation="Existing values may not be compatible with the target BSON type.", evidence=(f"change_field_type specifies {field}: {old} -> {new}",), remediation="Define, test, and run a bounded conversion before enforcing the target type.", field=field))
    elif operation.name == "backfill":
        if set(operation.payload) != {"op", "field", "batched", "checkpointed", "throttled"}:
            return (_unsupported(operation, config),)
        field = operation.payload.get("field")
        safeguards = tuple(operation.payload.get(key) for key in ("batched", "checkpointed", "throttled"))
        if not _field_name(field) or not all(isinstance(value, bool) for value in safeguards):
            return (_unsupported(operation, config),)
        if not any(safeguards) and _rule_enabled("MONGO-DATA-001", config):
            findings.append(_finding(operation, config, rule_id="MONGO-DATA-001", title="Backfill has no declared execution bounds", default_severity=Severity.HIGH, confidence=Confidence.HIGH, explanation="An unbounded backfill can overload the target collection and be difficult to resume safely.", evidence=(f"backfill for field: {field} has no batching, checkpointing, or throttling",), remediation="Use bounded batches and declare checkpointing or throttling before deployment.", field=field))
    elif operation.name == "bulk_update":
        if set(operation.payload) != {"op", "filter_scope", "batched", "checkpointed", "throttled"} or operation.payload.get("filter_scope") not in {"all", "filtered"}:
            return (_unsupported(operation, config),)
        safeguards = tuple(operation.payload[key] for key in ("batched", "checkpointed", "throttled"))
        if not all(isinstance(value, bool) for value in safeguards):
            return (_unsupported(operation, config),)
        if not any(safeguards) and _rule_enabled("MONGO-DATA-001", config):
            findings.append(_finding(operation, config, rule_id="MONGO-DATA-001", title="Bulk update has no declared execution bounds", default_severity=Severity.HIGH, confidence=Confidence.HIGH, explanation="An unbounded multi-document update can overload the target collection and be difficult to resume safely.", evidence=(f"bulk_update specifies filter_scope: {operation.payload['filter_scope']} with no batching, checkpointing, or throttling",), remediation="Use bounded batches and declare checkpointing or throttling before deployment."))
    elif operation.name == "increment":
        if set(operation.payload) != {"op", "field", "idempotent"}:
            return (_unsupported(operation, config),)
        field, idempotent = operation.payload.get("field"), operation.payload.get("idempotent")
        if not _field_name(field) or not isinstance(idempotent, bool):
            return (_unsupported(operation, config),)
        if not idempotent and _rule_enabled("MONGO-EXEC-001", config):
            findings.append(_finding(operation, config, rule_id="MONGO-EXEC-001", title="Increment is not retry-idempotent", default_severity=Severity.MEDIUM, confidence=Confidence.HIGH, explanation="Retrying an increment can apply the mutation more than once.", evidence=(f"increment specifies idempotent: false for {field}",), remediation="Use an idempotency key or a retry-safe update strategy.", field=field))
    return tuple(findings)


def analyze(config: AuditorConfig) -> AnalysisReport:
    """Discover, parse, and statically analyze configured manifests without a database connection."""
    files = discover_manifests(config.inputs.migration_paths)
    findings: list[Finding] = []
    errors: list[str] = []
    for path in config.inputs.migration_paths:
        if not path.exists():
            errors.append(f"configured migration input not found: {_source_path(path)}")
        elif path.is_file() and not path.name.endswith(".migration.json"):
            errors.append(
                f"configured migration input is not a .migration.json file: {_source_path(path)}"
            )
    if not files and not errors:
        errors.append("no .migration.json manifests found in configured migration paths")
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
        "title": "Destructive collection or multi-document delete",
        "default_severity": Severity.HIGH.value,
    },
    {"rule_id": "MONGO-INDEX-002", "title": "Index build declared for a high-write collection", "default_severity": Severity.MEDIUM.value},
    {"rule_id": "MONGO-INDEX-003", "title": "Index removal may affect query performance", "default_severity": Severity.MEDIUM.value},
    {"rule_id": "MONGO-SCHEMA-001", "title": "Validator adds a required field", "default_severity": Severity.HIGH.value},
    {"rule_id": "MONGO-SCHEMA-002", "title": "Field removal or rename lacks a compatibility window", "default_severity": Severity.HIGH.value},
    {"rule_id": "MONGO-SCHEMA-003", "title": "Field type change lacks a conversion plan", "default_severity": Severity.HIGH.value},
    {"rule_id": "MONGO-DATA-001", "title": "Backfill has no declared execution bounds", "default_severity": Severity.HIGH.value},
    {"rule_id": "MONGO-EXEC-001", "title": "Increment is not retry-idempotent", "default_severity": Severity.MEDIUM.value},
    {
        "rule_id": "MONGO-ANALYSIS-001",
        "title": "Unsupported migration operation",
        "default_severity": Severity.INFO.value,
    },
)
