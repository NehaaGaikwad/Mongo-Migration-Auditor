"""Bounded, read-only checks against an explicitly configured MongoDB database."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from bson.int64 import Int64
from pymongo import MongoClient
from pymongo.errors import PyMongoError

from migration_auditor.config import PreflightConfig
from migration_auditor.models import Confidence, Finding, Severity, SourceLocation


@dataclass(frozen=True)
class PreflightCheck:
    collection: str
    operation: str
    status: str
    explanation: str

    def to_dict(self) -> dict[str, str]:
        return {"collection": self.collection, "operation": self.operation,
                "status": self.status, "explanation": self.explanation}


def _operations(files: tuple[Path, ...]) -> list[tuple[str, dict[str, Any], SourceLocation]]:
    result = []
    for path in files:
        try:
            manifest = json.loads(path.read_text(encoding="utf-8"))
            collection = manifest["collection"]
            for number, operation in enumerate(manifest["operations"], 1):
                if isinstance(operation, dict) and operation.get("op", operation.get("type")) in {
                    "create_index", "createIndex", "update_validator", "updateValidator"
                }:
                    result.append((collection, operation, SourceLocation(path.as_posix(), operation=f"operations[{number}]")))
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return result


def run_preflight(files: tuple[Path, ...], config: PreflightConfig) -> tuple[tuple[Finding, ...], tuple[PreflightCheck, ...]]:
    """Perform aggregation/find operations only; never call a MongoDB write API."""
    if not config.uri or not config.database:
        return (), (PreflightCheck("*", "connection", "skipped", "Preflight is not configured."),)
    checks: list[PreflightCheck] = []
    findings: list[Finding] = []
    try:
        client = MongoClient(config.uri, connectTimeoutMS=config.connect_timeout_ms,
                             serverSelectionTimeoutMS=config.server_selection_timeout_ms,
                             appname="mongo-migration-auditor-preflight")
    except Exception as exc:
        # Constructor errors may include the URI; report only the exception class.
        return (), (PreflightCheck("*", "connection", "incomplete",
                                   f"MongoDB preflight failed ({type(exc).__name__}); check the URI and connection settings."),)
    try:
        database = client[config.database]
        # Force connection now, so connection/auth failures are explicitly reported.
        database.command("ping")
        for collection_name, operation, location in _operations(files):
            op = operation.get("op", operation.get("type"))
            coll = database[collection_name]
            options = operation.get("options")
            unique = operation.get("unique", False) is True or (
                isinstance(options, dict) and options.get("unique") is True
            )
            if op in {"create_index", "createIndex"} and unique:
                keys = operation.get("keys")
                if not isinstance(keys, dict) or not keys or not all(isinstance(k, str) for k in keys):
                    checks.append(PreflightCheck(collection_name, location.operation or "", "unknown", "Unique index keys are unsupported."))
                    continue
                group_id = {key: f"${key}" for key in keys}
                pipeline = [{"$limit": config.max_documents}, {"$group": {"_id": group_id, "count": {"$sum": 1}}},
                            {"$match": {"count": {"$gt": 1}}}, {"$limit": config.max_duplicate_groups}]
                groups = list(coll.aggregate(pipeline, maxTimeMS=config.query_timeout_ms, allowDiskUse=False))
                if groups:
                    fields = ", ".join(keys)
                    findings.append(Finding.create(
                        rule_id="MONGO-PREFLIGHT-001", title="Duplicate values conflict with unique index",
                        severity=Severity.HIGH, confidence=Confidence.HIGH,
                        explanation="Duplicate indexed key groups exist in the inspected documents.",
                        evidence=(f"collection={collection_name}; fields={fields}; sampled_duplicate_groups={len(groups)}; values omitted",),
                        remediation="Resolve duplicate indexed values on staging before applying the unique index.",
                        documentation_url="docs/architecture.md#mongodb-preflight", suppressible=True,
                        location=location, collection=collection_name,
                        field=fields, index=operation.get("name")))
                    checks.append(PreflightCheck(collection_name, location.operation or "", "incomplete", f"Duplicate groups found; scan bounded to {config.max_documents} documents."))
                else:
                    checks.append(PreflightCheck(collection_name, location.operation or "", "sampled", f"No duplicate groups in at most {config.max_documents} documents; this is not a full-database guarantee."))
            elif op in {"update_validator", "updateValidator"}:
                validator = operation.get("validator")
                schema = validator.get("$jsonSchema") if isinstance(validator, dict) else None
                if not isinstance(schema, dict) or set(schema) - {"bsonType", "required", "properties"}:
                    checks.append(PreflightCheck(collection_name, location.operation or "", "unknown", "Validator syntax is unsupported; manual review required."))
                    continue
                required = schema.get("required", [])
                properties = schema.get("properties", {})
                root_type = schema.get("bsonType")
                if "bsonType" in schema and root_type != "object":
                    checks.append(PreflightCheck(collection_name, location.operation or "", "unknown", "Root bsonType constraints other than object are unsupported for collection documents; manual review required."))
                    continue
                if (
                    not isinstance(required, list)
                    or not all(isinstance(x, str) and x for x in required)
                    or not isinstance(properties, dict)
                    or not all(isinstance(name, str) and name for name in properties)
                ):
                    checks.append(PreflightCheck(collection_name, location.operation or "", "unknown", "Validator structure is unsupported; manual review required."))
                    continue
                supported_types = {"string", "bool", "int", "long", "double", "number", "object", "array", "null"}
                if any(not isinstance(spec, dict) or set(spec) != {"bsonType"} or spec.get("bsonType") not in supported_types for spec in properties.values()):
                    checks.append(PreflightCheck(collection_name, location.operation or "", "unknown", "Nested, multi-type, or unsupported property constraints require manual review."))
                    continue
                projection = {field: 1 for field in set(required) | set(properties)}
                violations = 0
                scanned = 0
                for document in coll.find({}, projection, max_time_ms=config.query_timeout_ms).limit(config.max_documents + 1):
                    scanned += 1
                    if scanned > config.max_documents:
                        break
                    bad = any(field not in document for field in required)
                    for field, spec in properties.items():
                        expected = spec.get("bsonType") if isinstance(spec, dict) else None
                        if expected and field in document:
                            value = document[field]
                            valid = {"string": lambda: isinstance(value, str), "bool": lambda: isinstance(value, bool),
                                     "int": lambda: type(value) is int,
                                     "long": lambda: isinstance(value, Int64),
                                     "double": lambda: isinstance(value, float), "number": lambda: isinstance(value, int | float) and not isinstance(value, bool),
                                     "object": lambda: isinstance(value, dict), "array": lambda: isinstance(value, list),
                                     "null": lambda: value is None}.get(expected)
                            if valid is None:
                                bad = True
                            elif not valid():
                                bad = True
                    violations += int(bad)
                status = "incomplete" if scanned > config.max_documents else "sampled"
                if violations:
                    findings.append(Finding.create(
                        rule_id="MONGO-PREFLIGHT-002", title="Documents violate proposed validator",
                        severity=Severity.HIGH, confidence=Confidence.HIGH,
                        explanation="Documents violate supported required-field or scalar type constraints.",
                        evidence=(f"collection={collection_name}; violating_documents={violations}; scanned={min(scanned, config.max_documents)}; values omitted",),
                        remediation="Repair incompatible documents or stage the validator rollout in phases.",
                        documentation_url="docs/architecture.md#mongodb-preflight", suppressible=True,
                        location=location, collection=collection_name))
                checks.append(PreflightCheck(collection_name, location.operation or "", status,
                    f"Scanned at most {config.max_documents} documents; violations={violations}."))
    except PyMongoError as exc:
        # Exception text can contain hosts/credentials; expose only its class.
        checks.append(PreflightCheck("*", "connection/query", "incomplete", f"MongoDB preflight failed ({type(exc).__name__}); check connectivity, authentication, permissions, and timeouts."))
    finally:
        client.close()
    return tuple(findings), tuple(checks)
