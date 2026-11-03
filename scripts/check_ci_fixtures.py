"""Exercise CI-facing auditor exit codes without a database or secrets."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _run(config: str, manifest: str, expected_exit: int, expected_status: str) -> None:
    with tempfile.TemporaryDirectory(prefix="migration-auditor-ci-") as temporary:
        directory = Path(temporary)
        (directory / "migrations").mkdir()
        (directory / "migrations" / "001.migration.json").write_text(manifest, encoding="utf-8")
        (directory / "migration-auditor.toml").write_text(config, encoding="utf-8")
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "migration_auditor",
                "check",
                "--config",
                str(directory / "migration-auditor.toml"),
                "--format",
                "json",
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
    if result.returncode != expected_exit:
        raise SystemExit(
            f"expected exit {expected_exit}, got {result.returncode}: {result.stdout}{result.stderr}"
        )
    report = json.loads(result.stdout)
    if report["status"] != expected_status:
        raise SystemExit(f"expected status {expected_status}, got {report['status']}")


def main() -> None:
    _run(
        '[inputs]\nmigration_paths = ["migrations"]\n',
        '{"version":1,"collection":"users","operations":[{"op":"create_index","keys":{"id":1}}]}',
        0,
        "completed",
    )
    _run(
        '[inputs]\nmigration_paths = ["migrations"]\n[policy]\nfail_on = "critical"\n',
        '{"version":1,"collection":"users","operations":[{"op":"drop_collection"}]}',
        0,
        "completed",
    )
    _run(
        '[inputs]\nmigration_paths = ["migrations"]\n[policy]\nfail_on = "high"\n',
        '{"version":1,"collection":"users","operations":[{"op":"drop_collection"}]}',
        1,
        "completed",
    )
    _run(
        '[inputs]\nmigration_paths = ["migrations"]\n',
        "{",
        2,
        "error",
    )
    print("Verified CI auditor fixtures: safe, warning, blocking, analyzer-error.")


if __name__ == "__main__":
    main()
