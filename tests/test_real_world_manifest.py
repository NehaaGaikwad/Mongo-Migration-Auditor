from __future__ import annotations

import json
from pathlib import Path

from migration_auditor.analysis import analyze
from migration_auditor.config import load_config


def _analyze(tmp_path: Path, operations: list[dict[str, object]]):
    manifest = tmp_path / "practical.migration.json"
    manifest.write_text(json.dumps({"version": 1, "collection": "users", "operations": operations}), encoding="utf-8")
    config = tmp_path / "migration-auditor.toml"
    config.write_text('[inputs]\nmigration_paths = ["practical.migration.json"]\n', encoding="utf-8")
    return analyze(load_config(config))


def test_common_create_index_and_backfill_aliases_detect_real_risks(tmp_path: Path) -> None:
    report = _analyze(tmp_path, [
        {"op": "createIndex", "keys": {"tenantId": 1, "email": 1}, "options": {"unique": True, "buildContext": "high_write"}},
        {"op": "backfill", "field": "normalizedEmail", "options": {}},
    ])
    assert report.errors == ()
    assert [finding.rule_id for finding in report.findings] == [
        "MONGO-INDEX-001", "MONGO-INDEX-002", "MONGO-DATA-001"
    ]


def test_mongo_style_bulk_and_validator_declarations_are_analyzed(tmp_path: Path) -> None:
    report = _analyze(tmp_path, [
        {"op": "updateMany", "filter": {"state": "old"}, "update": {"$set": {"state": "new"}}},
        {"op": "deleteMany", "filter": {}},
        {"op": "updateValidator", "validator": {"$jsonSchema": {"bsonType": "object", "required": ["email"]}}},
        {"op": "dropIndex", "name": "legacy_1"},
    ])
    assert {finding.rule_id for finding in report.findings} == {
        "MONGO-DATA-001", "MONGO-DATA-002", "MONGO-SCHEMA-001", "MONGO-INDEX-003"
    }


def test_unknown_or_ambiguous_driver_syntax_remains_unsupported(tmp_path: Path) -> None:
    report = _analyze(tmp_path, [{"op": "createIndex", "keys": {"email": 1}, "options": {"partialFilterExpression": {"active": True}}}])
    assert [finding.rule_id for finding in report.findings] == ["MONGO-ANALYSIS-001"]
