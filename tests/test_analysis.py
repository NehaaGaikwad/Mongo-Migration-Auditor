from pathlib import Path

from migration_auditor.analysis import analyze
from migration_auditor.config import load_config


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
