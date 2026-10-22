from __future__ import annotations

import json
from pathlib import Path

from migration_auditor.analysis import analyze
from migration_auditor.config import AuditorConfig, InputConfig


def _report(tmp_path: Path, operations: list[dict[str, object]]):
    manifest = tmp_path / "compatibility.migration.json"
    manifest.write_text(
        json.dumps({"version": 1, "collection": "users", "operations": operations}),
        encoding="utf-8",
    )
    return analyze(AuditorConfig(inputs=InputConfig(migration_paths=(manifest,))))


def test_standard_mongo_declarations_from_stress_manifest_are_analyzed(tmp_path: Path) -> None:
    report = _report(
        tmp_path,
        [
            {"type": "create_index", "keys": {"geo": "2dsphere"}, "name": "geo_location"},
            {"type": "create_index", "keys": {"searchText": "text"}, "name": "search_text"},
            {"type": "create_index", "keys": {"wildcard": "$**"}, "name": "wildcard_all_fields"},
            {"type": "drop_index", "keys": {"tenantId": 1, "missingField": 1}},
            {"type": "add_field", "field": "schemaVersion", "value": 1},
            {"type": "changeFieldType", "field": "createdAt", "from": "string", "to": "date", "conversionPlan": True},
            {"type": "createCollection", "collection": "temporary_import", "options": {"capped": True, "size": 1048576}},
            {"type": "increment", "field": "retryCount", "amount": 1, "idempotent": False},
            {"type": "increment", "field": "counter", "amount": 0, "idempotent": False},
        ],
    )

    assert [finding.rule_id for finding in report.findings] == [
        "MONGO-INDEX-003",
        "MONGO-EXEC-001",
    ]


def test_invalid_stress_manifest_values_remain_unsupported(tmp_path: Path) -> None:
    report = _report(
        tmp_path,
        [
            {"type": "create_index", "keys": {"wildcard": "wildcard"}},
            {"type": "create_collection", "collection": "x", "options": {"capped": True, "size": 0}},
            {"type": "increment", "field": "counter", "amount": "1", "idempotent": False},
            {"type": "increment", "field": "counter", "amount": 1e308, "idempotent": False},
        ],
    )

    assert [finding.rule_id for finding in report.findings] == [
        "MONGO-ANALYSIS-001",
        "MONGO-ANALYSIS-001",
        "MONGO-ANALYSIS-001",
        "MONGO-EXEC-001",
    ]
