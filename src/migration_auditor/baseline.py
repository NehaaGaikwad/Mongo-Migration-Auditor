"""Declarative baseline format, schema state tracking, and migration diff engine.

Milestone 5: Baseline-Aware Migration Analysis.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from migration_auditor.models import Confidence, Finding, Severity

BASELINE_VERSION = 1


class BaselineError(ValueError):
    """Raised when a baseline file cannot be read, parsed, or validated."""


@dataclass(frozen=True)
class IndexSpec:
    keys: tuple[tuple[str, int | str], ...]
    name: str | None = None
    unique: bool = False

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> IndexSpec:
        keys_raw = raw.get("keys")
        if not isinstance(keys_raw, Mapping) or not keys_raw:
            raise BaselineError("index must have non-empty 'keys' mapping")
        normalized_keys: list[tuple[str, int | str]] = []
        for k, v in keys_raw.items():
            if not isinstance(k, str) or not k.strip():
                raise BaselineError("index key field name must be a non-empty string")
            if v not in (1, -1, "2d", "2dsphere", "text", "hashed", "$**"):
                raise BaselineError(f"unsupported index direction/type '{v}' for key '{k}'")
            normalized_keys.append((k.strip(), v))
        name = raw.get("name")
        if name is not None and (not isinstance(name, str) or not name.strip()):
            raise BaselineError("index name must be a non-empty string if provided")
        unique = raw.get("unique", False)
        if not isinstance(unique, bool):
            raise BaselineError("index 'unique' must be a boolean")
        return cls(keys=tuple(normalized_keys), name=name.strip() if name else None, unique=unique)

    def to_dict(self) -> dict[str, Any]:
        return {
            "keys": dict(self.keys),
            "name": self.name,
            "unique": self.unique,
        }

    def key_dict(self) -> dict[str, int | str]:
        return dict(self.keys)


@dataclass(frozen=True)
class CollectionBaseline:
    name: str
    exists: bool = True
    indexes: tuple[IndexSpec, ...] = ()
    required_fields: tuple[str, ...] = ()
    field_types: tuple[tuple[str, str], ...] = ()
    properties: tuple[str, ...] = ()

    @classmethod
    def from_dict(cls, name: str, raw: Mapping[str, Any]) -> CollectionBaseline:
        if not isinstance(name, str) or not name.strip():
            raise BaselineError("collection name must be a non-empty string")
        if not isinstance(raw, Mapping):
            raise BaselineError(f"collection '{name}' definition must be an object")

        indexes_raw = raw.get("indexes", [])
        if not isinstance(indexes_raw, list):
            raise BaselineError(f"indexes for collection '{name}' must be an array")
        indexes = tuple(IndexSpec.from_dict(item) for item in indexes_raw)

        # Extract validator fields if provided
        required_fields: list[str] = []
        field_types: list[tuple[str, str]] = []
        properties: list[str] = []

        validator = raw.get("validator")
        if validator is not None:
            if not isinstance(validator, Mapping):
                raise BaselineError(f"validator for collection '{name}' must be an object")
            schema = validator.get("$jsonSchema", validator)
            if isinstance(schema, Mapping):
                req = schema.get("required", [])
                if isinstance(req, list):
                    for f in req:
                        if isinstance(f, str) and f.strip() and f.strip() not in required_fields:
                            required_fields.append(f.strip())
                props = schema.get("properties", {})
                if isinstance(props, Mapping):
                    for prop_name, prop_spec in props.items():
                        if isinstance(prop_name, str) and prop_name.strip():
                            p_clean = prop_name.strip()
                            properties.append(p_clean)
                            if isinstance(prop_spec, Mapping):
                                bson_type = prop_spec.get("bsonType")
                                if isinstance(bson_type, str) and bson_type.strip():
                                    field_types.append((p_clean, bson_type.strip()))

        # Also accept top-level properties or required if declared directly
        if "required" in raw and isinstance(raw["required"], list):
            for f in raw["required"]:
                if isinstance(f, str) and f.strip() and f.strip() not in required_fields:
                    required_fields.append(f.strip())

        return cls(
            name=name.strip(),
            exists=True,
            indexes=indexes,
            required_fields=tuple(required_fields),
            field_types=tuple(field_types),
            properties=tuple(sorted(set(properties) | set(required_fields))),
        )


@dataclass(frozen=True)
class BaselineSchema:
    version: int
    collections: Mapping[str, CollectionBaseline]

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> BaselineSchema:
        if not isinstance(raw, Mapping):
            raise BaselineError("baseline root must be an object")
        if "version" not in raw or type(raw["version"]) is not int or raw["version"] != BASELINE_VERSION:
            raise BaselineError(f"baseline requires integer version {BASELINE_VERSION}")

        collections_raw = raw.get("collections", {})
        collections: dict[str, CollectionBaseline] = {}
        if isinstance(collections_raw, Mapping):
            for coll_name, coll_def in collections_raw.items():
                collections[coll_name] = CollectionBaseline.from_dict(coll_name, coll_def)
        elif isinstance(collections_raw, list):
            for item in collections_raw:
                if not isinstance(item, Mapping) or "name" not in item:
                    raise BaselineError("collection array elements must be objects with 'name'")
                coll_name = item["name"]
                collections[coll_name] = CollectionBaseline.from_dict(coll_name, item)
        else:
            raise BaselineError("'collections' in baseline must be an object or array")

        return cls(version=BASELINE_VERSION, collections=collections)


def load_baseline(path: Path) -> BaselineSchema:
    """Load and validate a schema baseline JSON file."""
    try:
        content = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise BaselineError(f"{path.as_posix()}: baseline is not valid UTF-8") from exc
    except OSError as exc:
        raise BaselineError(f"{path.as_posix()}: could not read baseline file") from exc

    try:
        raw = json.loads(content)
    except json.JSONDecodeError as exc:
        raise BaselineError(f"{path.as_posix()}: invalid JSON in baseline file") from exc

    return BaselineSchema.from_dict(raw)


@dataclass(frozen=True)
class BaselineDiff:
    collection: str
    confidence: Confidence
    collections_created: tuple[str, ...] = ()
    collections_dropped: tuple[str, ...] = ()
    indexes_added: tuple[dict[str, Any], ...] = ()
    indexes_dropped: tuple[str, ...] = ()
    fields_added: tuple[str, ...] = ()
    fields_removed: tuple[str, ...] = ()
    fields_renamed: tuple[dict[str, str], ...] = ()
    type_changes: tuple[dict[str, str], ...] = ()
    validator_changes: tuple[dict[str, Any], ...] = ()
    notes: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "collection": self.collection,
            "confidence": self.confidence.value,
            "collections_created": list(self.collections_created),
            "collections_dropped": list(self.collections_dropped),
            "indexes_added": list(self.indexes_added),
            "indexes_dropped": list(self.indexes_dropped),
            "fields_added": list(self.fields_added),
            "fields_removed": list(self.fields_removed),
            "fields_renamed": list(self.fields_renamed),
            "type_changes": list(self.type_changes),
            "validator_changes": list(self.validator_changes),
            "notes": list(self.notes),
        }


class _MutableCollectionState:
    def __init__(self, name: str, known_in_baseline: bool, baseline: CollectionBaseline | None = None) -> None:
        self.name = name
        self.known_in_baseline = known_in_baseline
        if baseline:
            self.exists = baseline.exists
            self.indexes: list[IndexSpec] = list(baseline.indexes)
            self.required_fields: set[str] = set(baseline.required_fields)
            self.field_types: dict[str, str] = dict(baseline.field_types)
            self.properties: set[str] = set(baseline.properties)
        else:
            self.exists = False
            self.indexes = []
            self.required_fields = set()
            self.field_types = {}
            self.properties = set()

    def find_index(self, keys: Mapping[str, Any] | None = None, name: str | None = None) -> IndexSpec | None:
        target_keys = tuple(keys.items()) if keys is not None else None
        for idx in self.indexes:
            if name and idx.name == name:
                return idx
            if target_keys is not None and idx.keys == target_keys:
                return idx
        return None


class BaselineStateTracker:
    """Tracks sequential migrations across files and computes deterministic diffs against baseline."""

    def __init__(self, baseline: BaselineSchema) -> None:
        self.baseline = baseline
        self.states: dict[str, _MutableCollectionState] = {
            name: _MutableCollectionState(name, known_in_baseline=True, baseline=coll)
            for name, coll in baseline.collections.items()
        }
        self.diffs: dict[str, dict[str, list[Any]]] = {}
        self.notes: dict[str, list[str]] = {}
        self.findings: list[Finding] = []

    def _get_or_create_state(self, collection: str) -> _MutableCollectionState:
        if collection not in self.states:
            self.states[collection] = _MutableCollectionState(collection, known_in_baseline=False)
        return self.states[collection]

    def _get_diff_bucket(self, collection: str) -> dict[str, list[Any]]:
        if collection not in self.diffs:
            self.diffs[collection] = {
                "collections_created": [],
                "collections_dropped": [],
                "indexes_added": [],
                "indexes_dropped": [],
                "fields_added": [],
                "fields_removed": [],
                "fields_renamed": [],
                "type_changes": [],
                "validator_changes": [],
            }
            self.notes[collection] = []
        return self.diffs[collection]

    def apply_operation(self, op: Any) -> None:
        """Apply a normalized Operation against the evolving baseline state."""
        coll_name = op.collection
        state = self._get_or_create_state(coll_name)
        bucket = self._get_diff_bucket(coll_name)

        if not state.known_in_baseline:
            # Operation touches a collection not declared in the baseline.
            # Record explicit uncertainty rather than claiming safety.
            note = f"Collection '{coll_name}' is not declared in baseline schema; transition safety cannot be verified."
            if note not in self.notes[coll_name]:
                self.notes[coll_name].append(note)
                self.findings.append(
                    Finding.create(
                        rule_id="MONGO-BASELINE-001",
                        title="Baseline coverage incomplete: undeclared collection",
                        severity=Severity.LOW,
                        confidence=Confidence.UNKNOWN,
                        explanation=(
                            f"The collection '{coll_name}' is not declared in the baseline schema. "
                            "State transitions and baseline drift cannot be verified against prior state."
                        ),
                        evidence=(f"operation references undeclared collection: {coll_name}",),
                        remediation="Declare the collection in the baseline schema to enable deterministic drift analysis.",
                        documentation_url="docs/rules.md#mongo-baseline-001",
                        suppressible=True,
                        location=op.location,
                        collection=coll_name,
                    )
                )

        payload = op.payload

        if op.name == "create_collection":
            if state.known_in_baseline and state.exists:
                self.findings.append(
                    Finding.create(
                        rule_id="MONGO-BASELINE-001",
                        title="Baseline drift: collection already exists",
                        severity=Severity.HIGH,
                        confidence=Confidence.HIGH,
                        explanation=f"Cannot create collection '{coll_name}' because it already exists in the baseline.",
                        evidence=(f"create_collection on existing collection: {coll_name}",),
                        remediation="Remove redundant create_collection or update baseline if this migration is out of order.",
                        documentation_url="docs/rules.md#mongo-baseline-001",
                        suppressible=True,
                        location=op.location,
                        collection=coll_name,
                    )
                )
            state.exists = True
            bucket["collections_created"].append(coll_name)

        elif op.name == "drop_collection":
            if state.known_in_baseline and not state.exists:
                self.findings.append(
                    Finding.create(
                        rule_id="MONGO-BASELINE-001",
                        title="Baseline drift: dropping non-existent collection",
                        severity=Severity.HIGH,
                        confidence=Confidence.HIGH,
                        explanation=f"Cannot drop collection '{coll_name}' because it does not exist in the baseline state.",
                        evidence=(f"drop_collection on non-existent collection: {coll_name}",),
                        remediation="Verify migration sequence; ensure collection exists before dropping.",
                        documentation_url="docs/rules.md#mongo-baseline-001",
                        suppressible=True,
                        location=op.location,
                        collection=coll_name,
                    )
                )
            state.exists = False
            state.indexes.clear()
            state.properties.clear()
            state.required_fields.clear()
            state.field_types.clear()
            bucket["collections_dropped"].append(coll_name)

        elif op.name == "create_index":
            keys = payload.get("keys")
            name = payload.get("name")
            unique = payload.get("unique", False)
            if state.known_in_baseline and not state.exists:
                self.findings.append(
                    Finding.create(
                        rule_id="MONGO-BASELINE-001",
                        title="Baseline drift: index created on non-existent collection",
                        severity=Severity.HIGH,
                        confidence=Confidence.HIGH,
                        explanation=f"Cannot create index on collection '{coll_name}' because the collection does not exist in the baseline.",
                        evidence=(f"create_index on non-existent collection: {coll_name}",),
                        remediation="Ensure collection is created before adding indexes.",
                        documentation_url="docs/rules.md#mongo-baseline-001",
                        suppressible=True,
                        location=op.location,
                        collection=coll_name,
                    )
                )
            elif isinstance(keys, Mapping):
                existing = state.find_index(keys=keys, name=name)
                if existing is not None:
                    self.findings.append(
                        Finding.create(
                            rule_id="MONGO-BASELINE-001",
                            title="Baseline drift: duplicate index creation",
                            severity=Severity.HIGH,
                            confidence=Confidence.HIGH,
                            explanation=(
                                f"Index on {dict(existing.keys)} (name: {existing.name or '<unnamed>'}) "
                                f"already exists in collection '{coll_name}'."
                            ),
                            evidence=(f"duplicate create_index on keys: {dict(existing.keys)}",),
                            remediation="Remove redundant create_index operation from migration.",
                            documentation_url="docs/rules.md#mongo-baseline-001",
                            suppressible=True,
                            location=op.location,
                            collection=coll_name,
                            index=name,
                        )
                    )
                else:
                    new_idx = IndexSpec(
                        keys=tuple(keys.items()),
                        name=name,
                        unique=unique,
                    )
                    state.indexes.append(new_idx)
                    bucket["indexes_added"].append(new_idx.to_dict())

        elif op.name == "drop_index":
            name = payload.get("name")
            keys = payload.get("keys")
            if state.known_in_baseline and not state.exists:
                self.findings.append(
                    Finding.create(
                        rule_id="MONGO-BASELINE-001",
                        title="Baseline drift: drop index on non-existent collection",
                        severity=Severity.HIGH,
                        confidence=Confidence.HIGH,
                        explanation=f"Cannot drop index from non-existent collection '{coll_name}'.",
                        evidence=(f"drop_index on non-existent collection: {coll_name}",),
                        remediation="Verify collection exists before dropping its indexes.",
                        documentation_url="docs/rules.md#mongo-baseline-001",
                        suppressible=True,
                        location=op.location,
                        collection=coll_name,
                    )
                )
            elif state.known_in_baseline:
                existing = state.find_index(keys=keys, name=name)
                if existing is None:
                    idx_label = name or (str(dict(keys)) if keys else "unknown")
                    self.findings.append(
                        Finding.create(
                            rule_id="MONGO-BASELINE-001",
                            title="Baseline drift: drop non-existent index",
                            severity=Severity.HIGH,
                            confidence=Confidence.HIGH,
                            explanation=f"Index '{idx_label}' does not exist on collection '{coll_name}' in baseline state.",
                            evidence=(f"drop_index refers to missing index: {idx_label}",),
                            remediation="Check index name and keys against baseline before attempting removal.",
                            documentation_url="docs/rules.md#mongo-baseline-001",
                            suppressible=True,
                            location=op.location,
                            collection=coll_name,
                            index=idx_label,
                        )
                    )
                else:
                    state.indexes.remove(existing)
                    bucket["indexes_dropped"].append(existing.name or str(dict(existing.keys)))

        elif op.name == "add_field":
            field_name = payload.get("field")
            required = payload.get("required", False)
            if state.known_in_baseline and not state.exists:
                self.findings.append(
                    Finding.create(
                        rule_id="MONGO-BASELINE-001",
                        title="Baseline drift: add field to non-existent collection",
                        severity=Severity.HIGH,
                        confidence=Confidence.HIGH,
                        explanation=f"Cannot add field '{field_name}' to non-existent collection '{coll_name}'.",
                        evidence=(f"add_field on non-existent collection: {coll_name}",),
                        remediation="Create collection before adding fields.",
                        documentation_url="docs/rules.md#mongo-baseline-001",
                        suppressible=True,
                        location=op.location,
                        collection=coll_name,
                        field=field_name,
                    )
                )
            elif isinstance(field_name, str):
                if state.known_in_baseline and field_name in state.properties:
                    self.findings.append(
                        Finding.create(
                            rule_id="MONGO-BASELINE-001",
                            title="Baseline drift: field already exists in schema",
                            severity=Severity.HIGH,
                            confidence=Confidence.HIGH,
                            explanation=f"Field '{field_name}' already exists in the baseline schema for collection '{coll_name}'.",
                            evidence=(f"add_field on existing field: {field_name}",),
                            remediation="Verify field additions against baseline schema.",
                            documentation_url="docs/rules.md#mongo-baseline-001",
                            suppressible=True,
                            location=op.location,
                            collection=coll_name,
                            field=field_name,
                        )
                    )
                state.properties.add(field_name)
                if required:
                    state.required_fields.add(field_name)
                bucket["fields_added"].append(field_name)

        elif op.name == "remove_field":
            field_name = payload.get("field")
            if state.known_in_baseline and not state.exists:
                self.findings.append(
                    Finding.create(
                        rule_id="MONGO-BASELINE-001",
                        title="Baseline drift: remove field from non-existent collection",
                        severity=Severity.HIGH,
                        confidence=Confidence.HIGH,
                        explanation=f"Cannot remove field '{field_name}' from non-existent collection '{coll_name}'.",
                        evidence=(f"remove_field on non-existent collection: {coll_name}",),
                        remediation="Verify collection exists before modifying schema fields.",
                        documentation_url="docs/rules.md#mongo-baseline-001",
                        suppressible=True,
                        location=op.location,
                        collection=coll_name,
                        field=field_name,
                    )
                )
            elif state.known_in_baseline and isinstance(field_name, str):
                if field_name not in state.properties:
                    self.findings.append(
                        Finding.create(
                            rule_id="MONGO-BASELINE-001",
                            title="Baseline drift: remove non-existent field",
                            severity=Severity.HIGH,
                            confidence=Confidence.HIGH,
                            explanation=f"Field '{field_name}' does not exist in baseline schema for collection '{coll_name}'.",
                            evidence=(f"remove_field on non-existent field: {field_name}",),
                            remediation="Verify field existence in baseline schema before removing.",
                            documentation_url="docs/rules.md#mongo-baseline-001",
                            suppressible=True,
                            location=op.location,
                            collection=coll_name,
                            field=field_name,
                        )
                    )
                state.properties.discard(field_name)
                state.required_fields.discard(field_name)
                state.field_types.pop(field_name, None)
                bucket["fields_removed"].append(field_name)

        elif op.name == "rename_field":
            src = payload.get("from")
            dst = payload.get("to")
            if state.known_in_baseline and not state.exists:
                self.findings.append(
                    Finding.create(
                        rule_id="MONGO-BASELINE-001",
                        title="Baseline drift: rename field in non-existent collection",
                        severity=Severity.HIGH,
                        confidence=Confidence.HIGH,
                        explanation=f"Cannot rename field in non-existent collection '{coll_name}'.",
                        evidence=(f"rename_field on non-existent collection: {coll_name}",),
                        remediation="Verify collection exists before renaming fields.",
                        documentation_url="docs/rules.md#mongo-baseline-001",
                        suppressible=True,
                        location=op.location,
                        collection=coll_name,
                    )
                )
            elif state.known_in_baseline and isinstance(src, str) and isinstance(dst, str):
                if src not in state.properties:
                    self.findings.append(
                        Finding.create(
                            rule_id="MONGO-BASELINE-001",
                            title="Baseline drift: rename non-existent source field",
                            severity=Severity.HIGH,
                            confidence=Confidence.HIGH,
                            explanation=f"Source field '{src}' does not exist in baseline schema for collection '{coll_name}'.",
                            evidence=(f"rename_field from non-existent field: {src}",),
                            remediation="Confirm the original field name exists in the baseline schema.",
                            documentation_url="docs/rules.md#mongo-baseline-001",
                            suppressible=True,
                            location=op.location,
                            collection=coll_name,
                            field=src,
                        )
                    )
                if dst in state.properties:
                    self.findings.append(
                        Finding.create(
                            rule_id="MONGO-BASELINE-001",
                            title="Baseline drift: target field already exists",
                            severity=Severity.HIGH,
                            confidence=Confidence.HIGH,
                            explanation=f"Target field '{dst}' already exists in baseline schema for collection '{coll_name}'.",
                            evidence=(f"rename_field to already existing field: {dst}",),
                            remediation="Choose a target field name that does not collide with existing fields.",
                            documentation_url="docs/rules.md#mongo-baseline-001",
                            suppressible=True,
                            location=op.location,
                            collection=coll_name,
                            field=dst,
                        )
                    )
                state.properties.discard(src)
                state.properties.add(dst)
                if src in state.required_fields:
                    state.required_fields.discard(src)
                    state.required_fields.add(dst)
                if src in state.field_types:
                    val = state.field_types.pop(src)
                    state.field_types[dst] = val
                bucket["fields_renamed"].append({"from": src, "to": dst})

        elif op.name == "change_field_type":
            field_name = payload.get("field")
            from_type = payload.get("from_type")
            to_type = payload.get("to_type")
            if state.known_in_baseline and not state.exists:
                self.findings.append(
                    Finding.create(
                        rule_id="MONGO-BASELINE-001",
                        title="Baseline drift: change field type on non-existent collection",
                        severity=Severity.HIGH,
                        confidence=Confidence.HIGH,
                        explanation=f"Cannot change field type in non-existent collection '{coll_name}'.",
                        evidence=(f"change_field_type on non-existent collection: {coll_name}",),
                        remediation="Verify collection exists before altering field types.",
                        documentation_url="docs/rules.md#mongo-baseline-001",
                        suppressible=True,
                        location=op.location,
                        collection=coll_name,
                    )
                )
            elif state.known_in_baseline and isinstance(field_name, str):
                actual_type = state.field_types.get(field_name)
                if actual_type is not None and from_type != actual_type:
                    self.findings.append(
                        Finding.create(
                            rule_id="MONGO-BASELINE-001",
                            title="Baseline drift: mismatched current field type",
                            severity=Severity.HIGH,
                            confidence=Confidence.HIGH,
                            explanation=(
                                f"Field '{field_name}' in collection '{coll_name}' is declared as type '{actual_type}' "
                                f"in baseline schema, but migration expected '{from_type}'."
                            ),
                            evidence=(f"field type mismatch: baseline is {actual_type}, migration from_type is {from_type}",),
                            remediation="Align migration from_type with baseline schema.",
                            documentation_url="docs/rules.md#mongo-baseline-001",
                            suppressible=True,
                            location=op.location,
                            collection=coll_name,
                            field=field_name,
                        )
                    )
                if isinstance(to_type, str):
                    state.field_types[field_name] = to_type
                bucket["type_changes"].append({"field": field_name, "from": from_type, "to": to_type})

        elif op.name == "update_validator":
            added_required = payload.get("added_required", [])
            if state.known_in_baseline and not state.exists:
                self.findings.append(
                    Finding.create(
                        rule_id="MONGO-BASELINE-001",
                        title="Baseline drift: update validator on non-existent collection",
                        severity=Severity.HIGH,
                        confidence=Confidence.HIGH,
                        explanation=f"Cannot update validator on non-existent collection '{coll_name}'.",
                        evidence=(f"update_validator on non-existent collection: {coll_name}",),
                        remediation="Verify collection exists before updating validator.",
                        documentation_url="docs/rules.md#mongo-baseline-001",
                        suppressible=True,
                        location=op.location,
                        collection=coll_name,
                    )
                )
            elif isinstance(added_required, list):
                if state.known_in_baseline:
                    already_req = [f for f in added_required if f in state.required_fields]
                    if already_req:
                        self.findings.append(
                            Finding.create(
                                rule_id="MONGO-BASELINE-001",
                                title="Baseline drift: redundant required fields in validator",
                                severity=Severity.MEDIUM,
                                confidence=Confidence.HIGH,
                                explanation=(
                                    f"Field(s) {already_req} are already required in baseline schema for collection '{coll_name}'."
                                ),
                                evidence=(f"redundantly required fields: {', '.join(already_req)}",),
                                remediation="Review validator update to avoid redundant constraints.",
                                documentation_url="docs/rules.md#mongo-baseline-001",
                                suppressible=True,
                                location=op.location,
                                collection=coll_name,
                            )
                        )
                for f in added_required:
                    if isinstance(f, str):
                        state.required_fields.add(f)
                        state.properties.add(f)
                bucket["validator_changes"].append({"added_required": added_required})

    def build_diffs(self) -> tuple[BaselineDiff, ...]:
        """Produce deterministic BaselineDiff records for each touched collection."""
        result: list[BaselineDiff] = []
        for coll_name, bucket in self.diffs.items():
            state = self.states.get(coll_name)
            confidence = Confidence.HIGH if (state and state.known_in_baseline) else Confidence.UNKNOWN
            result.append(
                BaselineDiff(
                    collection=coll_name,
                    confidence=confidence,
                    collections_created=tuple(bucket["collections_created"]),
                    collections_dropped=tuple(bucket["collections_dropped"]),
                    indexes_added=tuple(bucket["indexes_added"]),
                    indexes_dropped=tuple(bucket["indexes_dropped"]),
                    fields_added=tuple(bucket["fields_added"]),
                    fields_removed=tuple(bucket["fields_removed"]),
                    fields_renamed=tuple(bucket["fields_renamed"]),
                    type_changes=tuple(bucket["type_changes"]),
                    validator_changes=tuple(bucket["validator_changes"]),
                    notes=tuple(self.notes.get(coll_name, [])),
                )
            )
        return tuple(result)
