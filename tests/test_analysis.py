import json
from pathlib import Path

import pytest

from migration_auditor.analysis import (
    AnalysisError,
    Operation,
    analyze,
    analyze_operation,
    parse_manifest,
)
from migration_auditor.config import RuleConfig, load_config
from migration_auditor.models import SourceLocation


def test_example_manifests_produce_risk_and_explicit_unsupported_result() -> None:
    config = load_config(Path("examples/milestone_3/migration-auditor.toml"))

    report = analyze(config)

    assert len(report.files) == 3
    assert report.errors == ()
    assert {finding.rule_id for finding in report.findings} == {
        "MONGO-ANALYSIS-001",
        "MONGO-INDEX-001",
    }
    assert report.unsupported_count == 1


def test_unknown_operation_is_not_silently_ignored(tmp_path: Path) -> None:
    migration_dir = tmp_path / "migrations"
    migration_dir.mkdir()
    (migration_dir / "dynamic.migration.json").write_text(
        '{"version": 1, "collection": "users", "operations": [{"op": "custom"}]}',
        encoding="utf-8",
    )
    config_path = tmp_path / "migration-auditor.toml"
    config_path.write_text('[inputs]\nmigration_paths = ["migrations"]\n', encoding="utf-8")

    report = analyze(load_config(config_path))

    assert [finding.rule_id for finding in report.findings] == ["MONGO-ANALYSIS-001"]


def test_invalid_json_is_an_analysis_error(tmp_path: Path) -> None:
    migration_dir = tmp_path / "migrations"
    migration_dir.mkdir()
    (migration_dir / "broken.migration.json").write_text("{", encoding="utf-8")
    config_path = tmp_path / "migration-auditor.toml"
    config_path.write_text('[inputs]\nmigration_paths = ["migrations"]\n', encoding="utf-8")

    report = analyze(load_config(config_path))

    assert report.successful is False
    assert len(report.errors) == 1
    assert report.errors[0].endswith("migrations/broken.migration.json: invalid JSON manifest")


@pytest.mark.parametrize("version", [True, False, "1", 1.0, None, [], {}])
def test_manifest_version_must_be_an_integer_one(tmp_path: Path, version: object) -> None:
    path = tmp_path / "invalid-version.migration.json"
    path.write_text(
        json.dumps({"version": version, "collection": "users", "operations": []}),
        encoding="utf-8",
    )

    with pytest.raises(AnalysisError, match="unsupported manifest version"):
        parse_manifest(path)


@pytest.mark.parametrize(
    "payload",
    [
        {"op": "create_index", "keys": {"": 1}},
        {"op": "create_index", "keys": {"email": True}},
        {"op": "create_index", "keys": {"email": 1.0}},
        {"op": "create_index", "keys": {"email": 1}, "name": 123},
        {"op": "create_index", "keys": {"email": 1}, "name": None},
        {"op": "create_index", "keys": {"email": 1}, "name": "  "},
        {"op": "create_index", "keys": {"email": 1}, "unique": "true"},
    ],
)
def test_malformed_index_fields_fail_closed(payload: dict[str, object]) -> None:
    operation = Operation(
        name="create_index",
        collection="users",
        payload=payload,
        location=SourceLocation(file="migration.migration.json"),
    )

    findings = analyze_operation(operation, RuleConfig())

    assert [finding.rule_id for finding in findings] == ["MONGO-ANALYSIS-001"]


def test_missing_migration_input_is_an_analysis_error(tmp_path: Path) -> None:
    config_path = tmp_path / "migration-auditor.toml"
    config_path.write_text('[inputs]\nmigration_paths = ["missing"]\n', encoding="utf-8")

    report = analyze(load_config(config_path))

    assert report.successful is False
    assert any("configured migration input not found" in error for error in report.errors)


def test_empty_migration_input_is_an_analysis_error(tmp_path: Path) -> None:
    config_path = tmp_path / "migration-auditor.toml"
    config_path.write_text('[inputs]\nmigration_paths = ["empty"]\n', encoding="utf-8")
    (tmp_path / "empty").mkdir()

    report = analyze(load_config(config_path))

    assert report.errors == ("no .migration.json manifests found in configured migration paths",)


def test_invalid_utf8_is_an_analysis_error(tmp_path: Path) -> None:
    path = tmp_path / "invalid-utf8.migration.json"
    path.write_bytes(b"\xff")

    with pytest.raises(AnalysisError, match="not valid UTF-8"):
        parse_manifest(path)


@pytest.mark.parametrize(
    ("payload", "rule_id"),
    [
        ({"op": "create_index", "keys": {"email": 1}, "build_context": "high_write"}, "MONGO-INDEX-002"),
        ({"op": "update_validator", "added_required": ["email"]}, "MONGO-SCHEMA-001"),
        ({"op": "remove_field", "field": "legacy", "compatibility_window": False}, "MONGO-SCHEMA-002"),
        ({"op": "rename_field", "from": "old", "to": "new", "compatibility_window": False}, "MONGO-SCHEMA-002"),
        ({"op": "change_field_type", "field": "age", "from_type": "string", "to_type": "int", "conversion_plan": False}, "MONGO-SCHEMA-003"),
        ({"op": "delete_many", "filter_scope": "filtered", "recovery_plan": True}, "MONGO-DATA-002"),
        ({"op": "backfill", "field": "status", "batched": False, "checkpointed": False, "throttled": False}, "MONGO-DATA-001"),
        ({"op": "increment", "field": "retry_count", "idempotent": False}, "MONGO-EXEC-001"),
    ],
)
def test_milestone_four_risks_are_detected(
    payload: dict[str, object], rule_id: str
) -> None:
    operation = Operation(
        name=str(payload["op"]),
        collection="users",
        payload=payload,
        location=SourceLocation(file="migration.migration.json"),
    )

    findings = analyze_operation(operation, RuleConfig())

    assert rule_id in {finding.rule_id for finding in findings}


@pytest.mark.parametrize(
    "payload",
    [
        {"op": "create_index", "keys": {"email": 1}, "build_context": "unknown"},
        {"op": "update_validator", "added_required": []},
        {"op": "remove_field", "field": "legacy", "compatibility_window": True},
        {"op": "rename_field", "from": "old", "to": "new", "compatibility_window": True},
        {"op": "change_field_type", "field": "age", "from_type": "string", "to_type": "int", "conversion_plan": True},
        {"op": "backfill", "field": "status", "batched": True, "checkpointed": False, "throttled": False},
        {"op": "increment", "field": "retry_count", "idempotent": True},
    ],
)
def test_milestone_four_safe_declarations_do_not_emit_risks(payload: dict[str, object]) -> None:
    operation = Operation(
        name=str(payload["op"]),
        collection="users",
        payload=payload,
        location=SourceLocation(file="migration.migration.json"),
    )

    assert analyze_operation(operation, RuleConfig()) == ()


@pytest.mark.parametrize(
    "payload",
    [
        {"op": "backfill", "field": "status", "batched": "yes", "checkpointed": False, "throttled": False},
        {"op": "update_validator", "added_required": ["email", "email"]},
        {"op": "delete_many", "filter_scope": "custom", "recovery_plan": True},
        {"op": "increment", "field": "retry_count", "idempotent": 1},
    ],
)
def test_milestone_four_malformed_operations_fail_closed(payload: dict[str, object]) -> None:
    operation = Operation(
        name=str(payload["op"]), collection="users", payload=payload,
        location=SourceLocation(file="migration.migration.json"),
    )

    assert [finding.rule_id for finding in analyze_operation(operation, RuleConfig())] == [
        "MONGO-ANALYSIS-001"
    ]
