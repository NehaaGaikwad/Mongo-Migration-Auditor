from __future__ import annotations

from pathlib import Path

from migration_auditor.analysis import analyze
from migration_auditor.config import AuditorConfig, InputConfig

FIXTURE = Path("examples/real_world/extensive-risk-test.migration.json")


def test_extensive_real_world_manifest_uses_supported_rules() -> None:
    report = analyze(AuditorConfig(inputs=InputConfig(migration_paths=(FIXTURE.resolve(),))))

    assert report.errors == ()
    assert [finding.rule_id for finding in report.findings] == [
        "MONGO-INDEX-001",
        "MONGO-INDEX-002",
        "MONGO-DATA-001",
        "MONGO-DATA-001",
        "MONGO-DATA-001",
    ]
    assert report.unsupported_count == 0
    assert report.malformed_count == 0


def test_write_rate_is_strict_and_conflicts_fail_closed(tmp_path: Path) -> None:
    manifest = tmp_path / "invalid-rate.migration.json"
    manifest.write_text(
        '{"version":1,"collection":"users","operations":['
        '{"type":"create_index","keys":{"lastLoginAt":-1},"write_rate":"medium"},'
        '{"type":"create_index","keys":{"lastLoginAt":-1},"write_rate":"high","build_context":"unknown"}'
        ']}',
        encoding="utf-8",
    )
    report = analyze(AuditorConfig(inputs=InputConfig(migration_paths=(manifest,))))

    assert [finding.rule_id for finding in report.findings] == [
        "MONGO-ANALYSIS-001",
        "MONGO-ANALYSIS-001",
    ]
