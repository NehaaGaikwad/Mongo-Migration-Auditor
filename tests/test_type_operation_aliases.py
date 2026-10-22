from __future__ import annotations

import json
from pathlib import Path

from migration_auditor.analysis import analyze
from migration_auditor.config import load_config


def _report(tmp_path: Path, operations: list[dict[str, object]]):
    manifest = tmp_path / "type-aliases.migration.json"
    manifest.write_text(
        json.dumps({"version": 1, "collection": "users", "operations": operations}),
        encoding="utf-8",
    )
    config = tmp_path / "migration-auditor.toml"
    config.write_text('[inputs]\nmigration_paths = ["type-aliases.migration.json"]\n', encoding="utf-8")
    return analyze(load_config(config))


def test_type_based_mongo_operations_use_existing_rules(tmp_path: Path) -> None:
    report = _report(
        tmp_path,
        [
            {"type": "createIndex", "keys": {"email": 1}, "options": {"unique": True}},
            {"type": "createIndex", "keys": {"createdAt": 1}},
            {"type": "backfill", "field": "normalizedStatus"},
        ],
    )

    assert report.errors == ()
    assert [finding.rule_id for finding in report.findings] == [
        "MONGO-INDEX-001",
        "MONGO-DATA-001",
    ]
    assert all(finding.rule_id != "MONGO-ANALYSIS-001" for finding in report.findings)


def test_conflicting_type_and_op_fail_closed(tmp_path: Path) -> None:
    report = _report(tmp_path, [{"op": "backfill", "type": "createIndex", "field": "status"}])

    assert [finding.rule_id for finding in report.findings] == ["MONGO-ANALYSIS-001"]
