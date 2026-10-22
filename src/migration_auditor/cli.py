"""Command-line interface for offline migration-manifest checks."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from migration_auditor import __version__
from migration_auditor.analysis import RULES, analyze
from migration_auditor.config import ConfigurationError, load_config
from migration_auditor.models import Severity

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
    for finding in report.findings:
        location = finding.location.file if finding.location else "<unknown>"
        operation = f" ({finding.location.operation})" if finding.location else ""
        lines.append(f"{finding.severity.upper()} {finding.rule_id} {location}{operation}: {finding.title}")
        lines.append(f"  Evidence: {finding.evidence[0]}")
        lines.append(f"  Remediation: {finding.remediation}")
    for error in report.errors:
        lines.append(f"ERROR: {error}")
    return "\n".join(lines)


def _run_check(args: argparse.Namespace) -> int:
    try:
        config = load_config(args.config)
    except ConfigurationError as exc:
        error = {"status": "error", "errors": [str(exc)]}
        print(json.dumps(error) if args.format == "json" else f"ERROR: {exc}")
        return EXIT_ANALYSIS_ERROR
    report = analyze(config)
    if args.format == "json":
        print(json.dumps(report.to_dict(), indent=2, sort_keys=True))
    else:
        print(_render_text(report))
    if not report.successful:
        return EXIT_ANALYSIS_ERROR
    return EXIT_POLICY_FAILURE if report.has_unanalyzable_input or _policy_failure(report.findings, config.policy.fail_on) else EXIT_OK


def main(argv: Sequence[str] | None = None) -> int:
    """Run the CLI and return a CI-compatible process status."""
    args = _parser().parse_args(argv)
    if args.command == "version":
        print(__version__)
        return EXIT_OK
    if args.command == "rules":
        print(json.dumps({"rules": RULES}, indent=2, sort_keys=True))
        return EXIT_OK
    return _run_check(args)


if __name__ == "__main__":
    raise SystemExit(main())
