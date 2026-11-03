"""Command-line interface for offline migration-manifest checks."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any

from migration_auditor import __version__
from migration_auditor.analysis import RULES, analyze
from migration_auditor.audit_store import AuditStoreError, MongoAuditStore, build_audit_store
from migration_auditor.config import ConfigurationError, load_config
from migration_auditor.models import AuditRun, RunStatus, Severity, StoredRun

EXIT_OK = 0
EXIT_POLICY_FAILURE = 1
EXIT_ANALYSIS_ERROR = 2
_SEVERITY_ORDER = {
    Severity.INFO: 0,
    Severity.LOW: 1,
    Severity.MEDIUM: 2,
    Severity.HIGH: 3,
    Severity.CRITICAL: 4,
}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="migration-auditor")
    subcommands = parser.add_subparsers(dest="command", required=True)
    check = subcommands.add_parser("check", help="check configured JSON migration manifests")
    check.add_argument("--config", type=Path, default=Path("migration-auditor.toml"))
    check.add_argument("--format", choices=("text", "json"), default="text")
    history = subcommands.add_parser("history", help="list recent persisted audit run summaries")
    history.add_argument("--config", type=Path, default=Path("migration-auditor.toml"))
    history.add_argument("--limit", type=int, default=20)
    history.add_argument("--format", choices=("text", "json"), default="text")
    history.add_argument("--by-rule", action="store_true", help="aggregate recurring findings by rule and severity")
    subcommands.add_parser("rules", help="list currently implemented rules")
    subcommands.add_parser("version", help="print the auditor version")
    return parser


def _policy_failure(findings: Sequence[Any], threshold: Severity) -> bool:
    return any(_SEVERITY_ORDER[Severity(finding.severity)] >= _SEVERITY_ORDER[threshold] for finding in findings)


def _render_text(report: Any) -> str:
    lines = [
        f"Files analyzed: {len(report.files)}",
        f"Findings: {len(report.findings)}",
        f"Unsupported operations: {report.unsupported_count}",
        f"Malformed operations: {report.malformed_count}",
    ]
    if getattr(report, "baseline_diffs", None):
        lines.append(f"Baseline collections tracked: {len(report.baseline_diffs)}")
        for diff in report.baseline_diffs:
            lines.append(f"  Collection '{diff.collection}' (confidence: {diff.confidence.value})")
            if diff.collections_created:
                lines.append(f"    Created: {', '.join(diff.collections_created)}")
            if diff.collections_dropped:
                lines.append(f"    Dropped: {', '.join(diff.collections_dropped)}")
            if diff.indexes_added:
                idx_names = [idx.get("name") or str(idx.get("keys")) for idx in diff.indexes_added]
                lines.append(f"    Indexes added: {', '.join(idx_names)}")
            if diff.indexes_dropped:
                lines.append(f"    Indexes dropped: {', '.join(diff.indexes_dropped)}")
            if diff.fields_added:
                lines.append(f"    Fields added: {', '.join(diff.fields_added)}")
            if diff.fields_removed:
                lines.append(f"    Fields removed: {', '.join(diff.fields_removed)}")
            if diff.fields_renamed:
                renames = [f"{r['from']} -> {r['to']}" for r in diff.fields_renamed]
                lines.append(f"    Fields renamed: {', '.join(renames)}")
            if diff.type_changes:
                types = [f"{t['field']}: {t['from']} -> {t['to']}" for t in diff.type_changes]
                lines.append(f"    Type changes: {', '.join(types)}")
            if diff.notes:
                for note in diff.notes:
                    lines.append(f"    Note: {note}")
    for finding in report.findings:
        location = finding.location.file if finding.location else "<unknown>"
        operation = f" ({finding.location.operation})" if finding.location else ""
        lines.append(f"{finding.severity.upper()} {finding.rule_id} {location}{operation}: {finding.title}")
        lines.append(f"  Evidence: {finding.evidence[0]}")
        lines.append(f"  Remediation: {finding.remediation}")
    for check in report.preflight_checks:
        lines.append(f"PREFLIGHT {check.status.upper()} {check.collection} {check.operation}: {check.explanation}")
    for error in report.errors:
        lines.append(f"ERROR: {error}")
    return "\n".join(lines)


def _config_fingerprint(config: Any) -> str:
    """Fingerprint analysis-relevant configuration without serializing credentials."""
    safe_config = {
        "inputs": {
            "migration_paths": [path.as_posix() for path in config.inputs.migration_paths],
            "baseline_path": config.inputs.baseline_path.as_posix()
            if config.inputs.baseline_path
            else None,
            "max_manifest_bytes": config.inputs.max_manifest_bytes,
            "max_operations_per_manifest": config.inputs.max_operations_per_manifest,
        },
        "rules": {
            "enabled": list(config.rules.enabled),
            "disabled": list(config.rules.disabled),
            "severity_overrides": {
                rule_id: severity.value
                for rule_id, severity in sorted(config.rules.severity_overrides.items())
            },
        },
        "policy": {"fail_on": config.policy.fail_on.value},
        "audit_store": {
            "database": config.audit_store.database,
            "runs_collection": config.audit_store.runs_collection,
            "findings_collection": config.audit_store.findings_collection,
            "retention_days": config.audit_store.retention_days,
        },
        "preflight": {
            "database": config.preflight.database,
            "connect_timeout_ms": config.preflight.connect_timeout_ms,
            "server_selection_timeout_ms": config.preflight.server_selection_timeout_ms,
            "query_timeout_ms": config.preflight.query_timeout_ms,
            "max_documents": config.preflight.max_documents,
            "max_duplicate_groups": config.preflight.max_duplicate_groups,
        },
        "suppressions": [
            {
                "finding_id": suppression.finding_id,
                "reason": suppression.reason,
                "expires_on": suppression.expires_on.isoformat(),
            }
            for suppression in config.suppressions
        ],
    }
    return sha256(json.dumps(safe_config, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _persist_report(config: Any, report: Any, started_at: datetime, completed_at: datetime) -> tuple[bool, str | None]:
    """Persist sanitized results when explicitly configured; offline mode remains a no-op."""
    if report.errors:
        status = RunStatus.FAILED
    elif report.successful:
        status = RunStatus.COMPLETED
    else:
        status = RunStatus.INCOMPLETE
    run = AuditRun.create(
        analyzer_version=__version__,
        started_at=started_at,
        completed_at=completed_at,
        status=status,
        config_fingerprint=_config_fingerprint(config),
        files_analyzed=len(report.files),
        findings_count=len(report.findings),
        unknown_operations_count=report.unsupported_count + report.malformed_count,
    )
    store = None
    try:
        store = build_audit_store(config.audit_store)
        result = store.persist(
            StoredRun(
                run=run,
                findings=report.findings,
                suppressed_findings=tuple(item.to_dict() for item in report.suppressed_findings),
            )
        )
        return result.persisted, None
    except AuditStoreError as exc:
        return False, str(exc)
    finally:
        if store is not None and hasattr(store, "close"):
            store.close()


def _run_check(args: argparse.Namespace) -> int:
    try:
        config = load_config(args.config)
    except ConfigurationError as exc:
        error = {"status": "error", "errors": [str(exc)]}
        print(json.dumps(error) if args.format == "json" else f"ERROR: {exc}")
        return EXIT_ANALYSIS_ERROR
    started_at = datetime.now(UTC)
    report = analyze(config)
    persisted, persistence_error = _persist_report(config, report, started_at, datetime.now(UTC))
    if args.format == "json":
        output = report.to_dict()
        output["persistence"] = {
            "status": "error" if persistence_error else ("persisted" if persisted else "offline"),
            "error": persistence_error,
        }
        print(json.dumps(output, indent=2, sort_keys=True))
    else:
        print(_render_text(report))
        print(
            "PERSISTENCE "
            + (f"ERROR: {persistence_error}" if persistence_error else ("STORED" if persisted else "OFFLINE"))
        )
    if persistence_error:
        return EXIT_ANALYSIS_ERROR
    if not report.successful:
        return EXIT_ANALYSIS_ERROR
    return EXIT_POLICY_FAILURE if report.has_unanalyzable_input or _policy_failure(report.findings, config.policy.fail_on) else EXIT_OK


def _run_history(args: argparse.Namespace) -> int:
    try:
        config = load_config(args.config)
        if not config.audit_store.enabled:
            raise ConfigurationError("audit history requires audit_store.uri")
        store = build_audit_store(config.audit_store)
        if not isinstance(store, MongoAuditStore):
            raise ConfigurationError("audit history requires MongoDB audit metadata persistence")
        try:
            result = store.recurring_findings(args.limit) if args.by_rule else store.recent_runs(args.limit)
        finally:
            store.close()
    except (ConfigurationError, AuditStoreError, ValueError) as exc:
        error = {"status": "error", "errors": [str(exc)]}
        print(json.dumps(error) if args.format == "json" else f"ERROR: {exc}")
        return EXIT_ANALYSIS_ERROR
    if args.format == "json":
        print(json.dumps({"recurring_findings" if args.by_rule else "runs": result}, indent=2, sort_keys=True))
    elif not result:
        print("No recurring findings." if args.by_rule else "No persisted audit runs.")
    elif args.by_rule:
        for finding in result:
            print(f"{finding['rule_id']} {finding['severity']} count={finding['count']}")
    else:
        for run in result:
            print(
                f"{run['completed_at']} {run['run_id']} {run['status']} "
                f"findings={run['findings_count']} files={run['files_analyzed']}"
            )
    return EXIT_OK


def main(argv: Sequence[str] | None = None) -> int:
    """Run the CLI and return a CI-compatible process status."""
    args = _parser().parse_args(argv)
    if args.command == "version":
        print(__version__)
        return EXIT_OK
    if args.command == "rules":
        print(json.dumps({"rules": RULES}, indent=2, sort_keys=True))
        return EXIT_OK
    if args.command == "history":
        return _run_history(args)
    return _run_check(args)


if __name__ == "__main__":
    raise SystemExit(main())
