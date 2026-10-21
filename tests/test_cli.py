import json
from pathlib import Path

from migration_auditor.cli import EXIT_ANALYSIS_ERROR, EXIT_OK, EXIT_POLICY_FAILURE, main


def _write_config(tmp_path: Path, manifest: str, *, fail_on: str = "high") -> Path:
    migrations = tmp_path / "migrations"
    migrations.mkdir()
    (migrations / "001.migration.json").write_text(manifest, encoding="utf-8")
    config = tmp_path / "migration-auditor.toml"
    config.write_text(
        f'[inputs]\nmigration_paths = ["migrations"]\n[policy]\nfail_on = "{fail_on}"\n',
        encoding="utf-8",
    )
    return config


def test_check_returns_zero_for_safe_manifest(tmp_path: Path, capsys: object) -> None:
    config = _write_config(
        tmp_path,
        '{"version": 1, "collection": "users", "operations": [{"op": "create_index", "keys": {"id": 1}}]}',
    )

    result = main(["check", "--config", str(config)])

    assert result == EXIT_OK
    assert "Findings: 0" in capsys.readouterr().out  # type: ignore[attr-defined]


def test_check_returns_policy_failure_and_json_report(tmp_path: Path, capsys: object) -> None:
    config = _write_config(
        tmp_path,
        '{"version": 1, "collection": "users", "operations": [{"op": "drop_collection"}]}',
    )

    result = main(["check", "--config", str(config), "--format", "json"])

    output = json.loads(capsys.readouterr().out)  # type: ignore[attr-defined]
    assert result == EXIT_POLICY_FAILURE
    assert output["findings"][0]["rule_id"] == "MONGO-DATA-002"


def test_check_keeps_warning_non_blocking_when_threshold_is_higher(tmp_path: Path, capsys: object) -> None:
    config = _write_config(
        tmp_path,
        '{"version": 1, "collection": "users", "operations": [{"op": "drop_collection"}]}',
        fail_on="critical",
    )

    result = main(["check", "--config", str(config)])

    assert result == EXIT_OK
    assert "MONGO-DATA-002" in capsys.readouterr().out  # type: ignore[attr-defined]


def test_check_uses_distinct_error_exit_code(tmp_path: Path, capsys: object) -> None:
    config = _write_config(tmp_path, "{")

    result = main(["check", "--config", str(config)])

    assert result == EXIT_ANALYSIS_ERROR
    assert "ERROR:" in capsys.readouterr().out  # type: ignore[attr-defined]


def test_rules_and_version_commands(capsys: object) -> None:
    assert main(["rules"]) == EXIT_OK
    assert "MONGO-INDEX-001" in capsys.readouterr().out  # type: ignore[attr-defined]
    assert main(["version"]) == EXIT_OK
    assert capsys.readouterr().out.strip() == "0.1.0"  # type: ignore[attr-defined]
