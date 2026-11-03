from __future__ import annotations

import json
from pathlib import Path

import pytest

from migration_auditor.analysis import analyze
from migration_auditor.baseline import (
    BaselineError,
    load_baseline,
)
from migration_auditor.cli import EXIT_POLICY_FAILURE, main
from migration_auditor.config import load_config
from migration_auditor.models import Confidence, Severity


def _write_baseline(tmp_path: Path, collections: dict[str, object], *, version: int = 1) -> Path:
    baseline_path = tmp_path / "schema-baseline.json"
    content = {"version": version, "collections": collections}
    baseline_path.write_text(json.dumps(content), encoding="utf-8")
    return baseline_path


def _write_config(
    tmp_path: Path,
    manifests: list[tuple[str, dict[str, object]]],
    baseline_path: Path | None = None,
    fail_on: str = "high",
) -> Path:
    migration_dir = tmp_path / "migrations"
    migration_dir.mkdir(parents=True, exist_ok=True)
    for filename, content in manifests:
        (migration_dir / filename).write_text(json.dumps(content), encoding="utf-8")

    config_lines = [
        "[inputs]",
        'migration_paths = ["migrations"]',
    ]
    if baseline_path is not None:
        rel = baseline_path.relative_to(tmp_path)
        config_lines.append(f'baseline_path = "{rel.as_posix()}"')
    config_lines.extend([
        "",
        "[policy]",
        f'fail_on = "{fail_on}"',
    ])
    config_path = tmp_path / "migration-auditor.toml"
    config_path.write_text("\n".join(config_lines) + "\n", encoding="utf-8")
    return config_path


def test_baseline_parser_validates_structure_and_types(tmp_path: Path) -> None:
    path = _write_baseline(
        tmp_path,
        {
            "users": {
                "validator": {
                    "$jsonSchema": {
                        "bsonType": "object",
                        "required": ["email"],
                        "properties": {
                            "email": {"bsonType": "string"},
                            "age": {"bsonType": "int"},
                        },
                    }
                },
                "indexes": [
                    {"name": "_id_", "keys": {"_id": 1}},
                    {"name": "email_idx", "keys": {"email": 1}, "unique": True},
                ],
            }
        },
    )

    baseline = load_baseline(path)
    assert baseline.version == 1
    assert "users" in baseline.collections
    users = baseline.collections["users"]
    assert users.exists is True
    assert users.required_fields == ("email",)
    assert dict(users.field_types) == {"email": "string", "age": "int"}
    assert len(users.indexes) == 2


@pytest.mark.parametrize(
    ("raw", "error_match"),
    [
        ("not json", "invalid JSON in baseline"),
        (json.dumps({"version": 2, "collections": {}}), "baseline requires integer version 1"),
        (json.dumps({"version": "1", "collections": {}}), "baseline requires integer version 1"),
        (json.dumps({"version": 1, "collections": "invalid"}), "'collections' in baseline must be an object or array"),
        (json.dumps({"version": 1, "collections": {"users": {"indexes": [{"keys": {}}]}}}), "non-empty 'keys'"),
        (json.dumps({"version": 1, "collections": {"users": {"indexes": [{"keys": {"a": "invalid"}}]}}}), "unsupported index direction"),
        (json.dumps({"version": 1, "collections": {"users": {"validator": {"$jsonSchema": {"required": "email"}}}}}), "required fields"),
        (json.dumps({"version": 1, "collections": {"users": {"validator": {"$jsonSchema": {"properties": {"email": {"bsonType": "javascript"}}}}}}}), "unsupported bsonType"),
        (json.dumps({"version": 1, "collections": {"users": {"indexes": [{"keys": {"email": 1}}, {"keys": {"email": 1}}]}}}), "duplicate index keys"),
    ],
)
def test_baseline_parser_rejects_malformed_inputs(tmp_path: Path, raw: str, error_match: str) -> None:
    path = tmp_path / "bad-baseline.json"
    path.write_text(raw, encoding="utf-8")
    with pytest.raises(BaselineError, match=error_match):
        load_baseline(path)


def test_baseline_parser_rejects_duplicate_collection_array_entries(tmp_path: Path) -> None:
    path = tmp_path / "duplicate-collections.json"
    path.write_text(
        json.dumps({"version": 1, "collections": [{"name": "users"}, {"name": "users"}]}),
        encoding="utf-8",
    )

    with pytest.raises(BaselineError, match="duplicate collection"):
        load_baseline(path)


def test_sequential_migrations_produce_deterministic_diffs(tmp_path: Path) -> None:
    baseline_path = _write_baseline(
        tmp_path,
        {
            "users": {
                "validator": {
                    "$jsonSchema": {
                        "bsonType": "object",
                        "required": ["email"],
                        "properties": {
                            "email": {"bsonType": "string"},
                            "status": {"bsonType": "string"},
                        },
                    }
                },
                "indexes": [
                    {"name": "_id_", "keys": {"_id": 1}},
                ],
            }
        },
    )

    # 001 adds index and field
    m1 = {
        "version": 1,
        "collection": "users",
        "operations": [
            {"op": "create_index", "keys": {"email": 1}, "name": "email_1", "unique": True},
            {"op": "add_field", "field": "phone", "required": False},
        ],
    }
    # 002 renames field and changes type
    m2 = {
        "version": 1,
        "collection": "users",
        "operations": [
            {"op": "rename_field", "from": "status", "to": "state", "compatibility_window": True},
            {"op": "change_field_type", "field": "email", "from_type": "string", "to_type": "string", "conversion_plan": True},
        ],
    }

    config_path = _write_config(tmp_path, [("001_initial.migration.json", m1), ("002_update.migration.json", m2)], baseline_path)
    report = analyze(load_config(config_path))

    assert report.successful is True
    assert len(report.baseline_diffs) == 1
    diff = report.baseline_diffs[0]
    assert diff.collection == "users"
    assert diff.confidence == Confidence.HIGH
    assert len(diff.indexes_added) == 1
    assert diff.indexes_added[0]["name"] == "email_1"
    assert diff.fields_added == ("phone",)
    assert diff.fields_renamed == ({"from": "status", "to": "state"},)


def test_baseline_drift_detects_duplicate_index(tmp_path: Path) -> None:
    baseline_path = _write_baseline(
        tmp_path,
        {
            "users": {
                "indexes": [
                    {"name": "email_idx", "keys": {"email": 1}, "unique": True},
                ],
            }
        },
    )
    m = {
        "version": 1,
        "collection": "users",
        "operations": [
            {"op": "create_index", "keys": {"email": 1}, "unique": True},
        ],
    }
    config_path = _write_config(tmp_path, [("001_dup.migration.json", m)], baseline_path)
    report = analyze(load_config(config_path))

    baseline_findings = [f for f in report.findings if f.rule_id == "MONGO-BASELINE-001"]
    assert len(baseline_findings) == 1
    assert "duplicate index" in baseline_findings[0].title.lower()
    assert baseline_findings[0].severity == Severity.HIGH
    assert baseline_findings[0].confidence == Confidence.HIGH


def test_baseline_drift_detects_dropping_non_existent_index(tmp_path: Path) -> None:
    baseline_path = _write_baseline(
        tmp_path,
        {
            "users": {
                "indexes": [
                    {"name": "_id_", "keys": {"_id": 1}},
                ],
            }
        },
    )
    m = {
        "version": 1,
        "collection": "users",
        "operations": [
            {"op": "drop_index", "name": "ghost_index"},
        ],
    }
    config_path = _write_config(tmp_path, [("001_drop.migration.json", m)], baseline_path)
    report = analyze(load_config(config_path))

    baseline_findings = [f for f in report.findings if f.rule_id == "MONGO-BASELINE-001"]
    assert len(baseline_findings) == 1
    assert "drop non-existent index" in baseline_findings[0].title.lower()
    assert baseline_findings[0].severity == Severity.HIGH


def test_baseline_drift_detects_contradictory_field_and_type_transitions(tmp_path: Path) -> None:
    baseline_path = _write_baseline(
        tmp_path,
        {
            "users": {
                "validator": {
                    "$jsonSchema": {
                        "properties": {
                            "age": {"bsonType": "int"},
                        }
                    }
                }
            }
        },
    )
    m = {
        "version": 1,
        "collection": "users",
        "operations": [
            # removing field that does not exist in baseline
            {"op": "remove_field", "field": "non_existent", "compatibility_window": True},
            # renaming from field that does not exist
            {"op": "rename_field", "from": "ghost", "to": "new_ghost", "compatibility_window": True},
            # renaming to field that already exists
            {"op": "rename_field", "from": "age", "to": "age", "compatibility_window": True},
            # changing type when baseline type is int, but migration specifies from_type string
            {"op": "change_field_type", "field": "age", "from_type": "string", "to_type": "long", "conversion_plan": True},
        ],
    }
    config_path = _write_config(tmp_path, [("001_drift.migration.json", m)], baseline_path)
    report = analyze(load_config(config_path))

    baseline_findings = [f for f in report.findings if f.rule_id == "MONGO-BASELINE-001"]
    titles = [f.title for f in baseline_findings]
    assert any("remove non-existent field" in t.lower() for t in titles)
    assert any("rename non-existent source field" in t.lower() for t in titles)
    assert any("target field already exists" in t.lower() for t in titles)
    assert any("mismatched current field type" in t.lower() for t in titles)


def test_multi_migration_ordering_tracks_state_across_files(tmp_path: Path) -> None:
    baseline_path = _write_baseline(
        tmp_path,
        {
            "users": {
                "indexes": [{"name": "_id_", "keys": {"_id": 1}}],
            }
        },
    )
    # File 1 creates a new index
    m1 = {
        "version": 1,
        "collection": "users",
        "operations": [
            {"op": "create_index", "keys": {"username": 1}, "name": "user_idx", "unique": False},
        ],
    }
    # File 2 drops the index created in File 1
    m2 = {
        "version": 1,
        "collection": "users",
        "operations": [
            {"op": "drop_index", "name": "user_idx"},
        ],
    }
    # File 3 drops it AGAIN -> must flag baseline drift because state tracker knows it was dropped in File 2!
    m3 = {
        "version": 1,
        "collection": "users",
        "operations": [
            {"op": "drop_index", "name": "user_idx"},
        ],
    }
    config_path = _write_config(
        tmp_path,
        [
            ("001_add_idx.migration.json", m1),
            ("002_drop_idx.migration.json", m2),
            ("003_drop_again.migration.json", m3),
        ],
        baseline_path,
    )
    report = analyze(load_config(config_path))
    baseline_findings = [f for f in report.findings if f.rule_id == "MONGO-BASELINE-001"]
    assert len(baseline_findings) == 1
    assert "drop non-existent index" in baseline_findings[0].title.lower()
    assert baseline_findings[0].location.file.endswith("003_drop_again.migration.json")


def test_incomplete_baseline_produces_explicit_unknown_confidence_rather_than_false_safety(tmp_path: Path) -> None:
    # Baseline only declares 'users'
    baseline_path = _write_baseline(
        tmp_path,
        {
            "users": {
                "indexes": [{"name": "_id_", "keys": {"_id": 1}}],
            }
        },
    )
    # Migration modifies 'orders', which is NOT declared in baseline
    m = {
        "version": 1,
        "collection": "orders",
        "operations": [
            {"op": "create_index", "keys": {"order_id": 1}},
        ],
    }
    config_path = _write_config(tmp_path, [("001_orders.migration.json", m)], baseline_path)
    report = analyze(load_config(config_path))

    # Must have a baseline diff for 'orders' with UNKNOWN confidence
    diffs = [d for d in report.baseline_diffs if d.collection == "orders"]
    assert len(diffs) == 1
    assert diffs[0].confidence == Confidence.UNKNOWN
    assert any("not declared in baseline" in note.lower() for note in diffs[0].notes)

    # Must emit explicit finding alerting user to incomplete baseline
    uncertainty_findings = [
        f for f in report.findings
        if f.rule_id == "MONGO-BASELINE-001" and f.confidence == Confidence.UNKNOWN
    ]
    assert len(uncertainty_findings) == 1
    assert "incomplete" in uncertainty_findings[0].title.lower()
    assert "declare the collection in the baseline" in uncertainty_findings[0].remediation.lower()


def test_cli_check_reports_baseline_diff_and_drift_exit_code(tmp_path: Path, capsys: object) -> None:
    baseline_path = _write_baseline(
        tmp_path,
        {
            "accounts": {
                "indexes": [{"name": "acc_idx", "keys": {"acc_no": 1}}],
            }
        },
    )
    m = {
        "version": 1,
        "collection": "accounts",
        "operations": [
            # Duplicated index on accounts causes MONGO-BASELINE-001 (High severity)
            {"op": "create_index", "keys": {"acc_no": 1}},
        ],
    }
    config_path = _write_config(tmp_path, [("001_accounts.migration.json", m)], baseline_path)

    exit_code = main(["check", "--config", str(config_path)])
    output = capsys.readouterr().out

    assert exit_code == EXIT_POLICY_FAILURE
    assert "MONGO-BASELINE-001" in output
    assert "Baseline collections tracked:" in output
    assert "Collection 'accounts'" in output


def test_compound_index_matching_preserves_key_order(tmp_path: Path) -> None:
    baseline_path = _write_baseline(
        tmp_path,
        {
            "users": {
                "indexes": [
                    {"name": "a_b_idx", "keys": {"a": 1, "b": 1}},
                ],
            }
        },
    )
    # Reversed compound index {"b": 1, "a": 1} is distinct in MongoDB; must NOT be flagged as duplicate
    m1 = {
        "version": 1,
        "collection": "users",
        "operations": [
            {"op": "create_index", "keys": {"b": 1, "a": 1}, "name": "b_a_idx"},
        ],
    }
    # Identical compound index {"a": 1, "b": 1} IS duplicate; must be flagged
    m2 = {
        "version": 1,
        "collection": "users",
        "operations": [
            {"op": "create_index", "keys": {"a": 1, "b": 1}, "name": "a_b_duplicate"},
        ],
    }
    config_path = _write_config(
        tmp_path,
        [("001_reversed.migration.json", m1), ("002_duplicate.migration.json", m2)],
        baseline_path,
    )
    report = analyze(load_config(config_path))

    baseline_findings = [f for f in report.findings if f.rule_id == "MONGO-BASELINE-001"]
    assert len(baseline_findings) == 1
    assert "duplicate index" in baseline_findings[0].title.lower()
    # The duplicate is strictly on m2 (002_duplicate.migration.json), not m1
    assert baseline_findings[0].location.file.endswith("002_duplicate.migration.json")

    # Verify both were tracked in the diff
    diff = report.baseline_diffs[0]
    added_names = [idx.get("name") for idx in diff.indexes_added]
    assert "b_a_idx" in added_names


def test_create_drop_recreate_cleans_collection_state_and_allows_reindexing(tmp_path: Path) -> None:
    # Baseline with no 'items' collection
    baseline_path = _write_baseline(tmp_path, {})

    # 001: create collection and index
    m1 = {
        "version": 1,
        "collection": "items",
        "operations": [
            {"op": "create_collection"},
            {"op": "create_index", "keys": {"sku": 1}, "name": "sku_idx"},
        ],
    }
    # 002: drop collection
    m2 = {
        "version": 1,
        "collection": "items",
        "operations": [
            {"op": "drop_collection"},
        ],
    }
    # 003: recreate collection and add same index again
    m3 = {
        "version": 1,
        "collection": "items",
        "operations": [
            {"op": "create_collection"},
            {"op": "create_index", "keys": {"sku": 1}, "name": "sku_idx"},
        ],
    }
    config_path = _write_config(
        tmp_path,
        [
            ("001_create.migration.json", m1),
            ("002_drop.migration.json", m2),
            ("003_recreate.migration.json", m3),
        ],
        baseline_path,
    )
    report = analyze(load_config(config_path))

    # There should be NO false duplicate index finding and NO false duplicate collection finding on recreation
    drift_findings = [f for f in report.findings if f.rule_id == "MONGO-BASELINE-001" and f.severity == Severity.HIGH]
    assert len(drift_findings) == 0

    diff = [d for d in report.baseline_diffs if d.collection == "items"][0]
    assert "items" in diff.collections_created
    assert "items" in diff.collections_dropped
