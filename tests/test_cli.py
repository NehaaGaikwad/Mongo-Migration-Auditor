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


def test_check_reports_offline_persistence_in_json(tmp_path: Path, capsys: object) -> None:
    config = _write_config(
        tmp_path,
        '{"version": 1, "collection": "users", "operations": [{"op": "create_index", "keys": {"id": 1}}]}',
    )

    assert main(["check", "--config", str(config), "--format", "json"]) == EXIT_OK

    assert json.loads(capsys.readouterr().out)["persistence"] == {
        "status": "offline",
        "error": None,
    }


def test_check_persists_when_audit_store_is_enabled(tmp_path: Path, capsys: object, monkeypatch) -> None:
    import migration_auditor.cli as cli

    config = _write_config(
        tmp_path,
        '{"version": 1, "collection": "users", "operations": [{"op": "create_index", "keys": {"id": 1}}]}',
    )
    config.write_text(config.read_text(encoding="utf-8") + "[audit_store]\nuri = \"mongodb://example.invalid\"\n", encoding="utf-8")
    captured = {}

    class Store:
        def persist(self, stored_run):
            captured["run"] = stored_run.run
            captured["findings"] = stored_run.findings
            return type("Result", (), {"persisted": True})()

        def close(self):
            captured["closed"] = True

    monkeypatch.setattr(cli, "build_audit_store", lambda _: Store())

    assert main(["check", "--config", str(config), "--format", "json"]) == EXIT_OK
    output = json.loads(capsys.readouterr().out)

    assert output["persistence"] == {"status": "persisted", "error": None}
    assert captured["run"].files_analyzed == 1
    assert captured["run"].config_fingerprint
    assert captured["findings"] == ()
    assert captured["closed"] is True


def test_check_returns_analysis_error_for_persistence_failure(tmp_path: Path, capsys: object, monkeypatch) -> None:
    import migration_auditor.cli as cli
    from migration_auditor.audit_store import AuditStoreError

    config = _write_config(
        tmp_path,
        '{"version": 1, "collection": "users", "operations": [{"op": "create_index", "keys": {"id": 1}}]}',
    )
    config.write_text(config.read_text(encoding="utf-8") + "[audit_store]\nuri = \"mongodb://example.invalid\"\n", encoding="utf-8")

    def failed_store(_):
        raise AuditStoreError("could not store audit run metadata")

    monkeypatch.setattr(cli, "build_audit_store", failed_store)

    assert main(["check", "--config", str(config), "--format", "json"]) == EXIT_ANALYSIS_ERROR
    output = json.loads(capsys.readouterr().out)
    assert output["persistence"] == {
        "status": "error",
        "error": "could not store audit run metadata",
    }


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


def test_check_fails_closed_for_unsupported_operation(tmp_path: Path) -> None:
    config = _write_config(
        tmp_path,
        '{"version": 1, "collection": "users", "operations": [{"type": "runCommand", "command": {}}]}',
        fail_on="critical",
    )

    assert main(["check", "--config", str(config)]) == EXIT_POLICY_FAILURE


def test_rules_and_version_commands(capsys: object) -> None:
    assert main(["rules"]) == EXIT_OK
    assert "MONGO-INDEX-001" in capsys.readouterr().out  # type: ignore[attr-defined]
    assert main(["version"]) == EXIT_OK
    assert capsys.readouterr().out.strip() == "0.1.0"  # type: ignore[attr-defined]


def test_history_requires_explicit_audit_store(tmp_path: Path, capsys: object) -> None:
    config = _write_config(
        tmp_path,
        '{"version": 1, "collection": "users", "operations": []}',
    )

    assert main(["history", "--config", str(config), "--format", "json"]) == EXIT_ANALYSIS_ERROR
    assert "audit history requires audit_store.uri" in json.loads(capsys.readouterr().out)["errors"][0]


def test_history_by_rule_uses_aggregate_query(tmp_path: Path, capsys: object, monkeypatch) -> None:
    import migration_auditor.cli as cli

    config = _write_config(tmp_path, '{"version": 1, "collection": "users", "operations": []}')
    config.write_text(config.read_text(encoding="utf-8") + '[audit_store]\nuri = "mongodb://example.invalid"\n', encoding="utf-8")

    class Store:
        def recurring_findings(self, limit):
            assert limit == 20
            return [{"rule_id": "MONGO-DATA-002", "severity": "high", "count": 2}]

        def close(self):
            pass

    monkeypatch.setattr(cli, "build_audit_store", lambda _: Store())
    monkeypatch.setattr(cli, "MongoAuditStore", Store)

    assert main(["history", "--config", str(config), "--by-rule", "--format", "json"]) == EXIT_OK
    assert json.loads(capsys.readouterr().out) == {
        "recurring_findings": [{"rule_id": "MONGO-DATA-002", "severity": "high", "count": 2}]
    }
