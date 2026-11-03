"""Run Master Plan B01-B10 scenarios against the supported declarative subset."""

from __future__ import annotations

import json
import time
from pathlib import Path
from tempfile import TemporaryDirectory

from migration_auditor.analysis import analyze
from migration_auditor.config import AuditorConfig, InputConfig

SCENARIOS = {
    "B01": ({"op": "update_validator", "added_required": ["email"]}, "MONGO-SCHEMA-001"),
    "B02": ({"op": "rename_field", "from": "old", "to": "new", "compatibility_window": False}, "MONGO-SCHEMA-002"),
    "B03": ({"op": "change_field_type", "field": "age", "from_type": "string", "to_type": "int", "conversion_plan": False}, "MONGO-SCHEMA-003"),
    "B04": ({"op": "create_index", "keys": {"email": 1}, "unique": True}, "MONGO-INDEX-001"),
    "B05": ({"op": "create_index", "keys": {"email": 1}, "build_context": "high_write"}, "MONGO-INDEX-002"),
    "B06": ({"op": "backfill", "field": "status", "batched": False, "checkpointed": False, "throttled": False}, "MONGO-DATA-001"),
    "B07": ({"op": "increment", "field": "retries", "idempotent": False}, "MONGO-EXEC-001"),
    "B08": ({"op": "drop_index", "name": "email_1"}, "MONGO-INDEX-003"),
    "B09": ({"op": "runCommand", "command": {}}, "MONGO-ANALYSIS-001"),
}


def _run_scenario(identifier: str, operation: dict[str, object], expected_rule: str) -> dict[str, object]:
    with TemporaryDirectory(prefix=f"migration-auditor-{identifier.lower()}-") as temporary:
        manifest = Path(temporary) / f"{identifier}.migration.json"
        manifest.write_text(
            json.dumps({"version": 1, "collection": "users", "operations": [operation]}),
            encoding="utf-8",
        )
        started = time.perf_counter()
        report = analyze(AuditorConfig(inputs=InputConfig(migration_paths=(manifest,))))
        elapsed_ms = round((time.perf_counter() - started) * 1_000, 3)
    observed = [finding.rule_id for finding in report.findings]
    return {
        "id": identifier,
        "expected_rule": expected_rule,
        "observed_rules": observed,
        "passed": expected_rule in observed and not report.errors,
        "runtime_ms": elapsed_ms,
    }


def _run_baseline_scenario() -> dict[str, object]:
    with TemporaryDirectory(prefix="migration-auditor-b10-") as temporary:
        directory = Path(temporary)
        baseline = directory / "baseline.json"
        baseline.write_text(
            json.dumps({"version": 1, "collections": {"users": {"indexes": [{"keys": {"email": 1}}]}}}),
            encoding="utf-8",
        )
        manifest = directory / "B10.migration.json"
        manifest.write_text(
            json.dumps({"version": 1, "collection": "users", "operations": [{"op": "create_index", "keys": {"email": 1}}]}),
            encoding="utf-8",
        )
        started = time.perf_counter()
        report = analyze(AuditorConfig(inputs=InputConfig(migration_paths=(manifest,), baseline_path=baseline)))
        elapsed_ms = round((time.perf_counter() - started) * 1_000, 3)
    observed = [finding.rule_id for finding in report.findings]
    return {
        "id": "B10",
        "expected_rule": "MONGO-BASELINE-001",
        "observed_rules": observed,
        "passed": "MONGO-BASELINE-001" in observed and not report.errors,
        "runtime_ms": elapsed_ms,
    }


def main() -> None:
    results = [_run_scenario(identifier, *scenario) for identifier, scenario in SCENARIOS.items()]
    results.append(_run_baseline_scenario())
    print(json.dumps({"tool": "Mongo Migration Auditor", "scenarios": results}, indent=2, sort_keys=True))
    if not all(result["passed"] for result in results):
        raise SystemExit("one or more benchmark scenarios failed")


if __name__ == "__main__":
    main()
