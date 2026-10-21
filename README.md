# Database Schema Migration Auditor

A safety-focused CI/CD check for MongoDB migrations. The planned tool will inspect a deliberately small, documented subset of migration and schema/index inputs before deployment, identify known risks, and make uncertainty explicit.

> **Project status:** Milestone 1 — repository and reproducible setup. The analyzer, migration parser, findings, MongoDB persistence, and CLI have not been implemented yet. This repository currently verifies the package foundation only.

## Why this exists

Database migrations can introduce incompatible validators, destructive operations, unsafe backfills, and risky index changes. This project aims to provide deterministic, explainable checks that can run locally and in CI before a production deployment.

The central safety principle is: unsupported or dynamic input must never be silently reported as safe. Future reports will distinguish detected risks, no known risk in the supported subset, and input that could not be analyzed.

## Milestone 1 completion record

| Item | Status |
| --- | --- |
| Installable Python package layout | Complete |
| Pinned development tooling and minimal test/lint commands | Complete |
| GitHub Actions CI workflow | Complete |
| Placeholder-only environment configuration and local MongoDB guide | Complete |
| Scope, non-goals, and privacy/safety boundaries | Complete |
| Public repository URL / remote CI run | Pending repository publication |

The project uses the MIT License; see [LICENSE](LICENSE). A GitHub issue/milestone board cannot be created until this local repository is published; its prepared backlog and completion criteria are in [docs/project-board.md](docs/project-board.md).

## Requirements

- Python 3.11 or newer (CI tests 3.11 through 3.14)
- Git
- Docker Desktop only if you want a local MongoDB instance for later milestones

## Quick start

```powershell
git clone <your-repository-url>
Set-Location database-schema-migration-auditor
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
python -m ruff check .
python -m pytest
```

On macOS/Linux, activate the environment with `source .venv/bin/activate`.

The project intentionally has no runtime dependencies in Milestone 1. Development dependencies are pinned in [pyproject.toml](pyproject.toml) for repeatability.

## Local MongoDB (optional)

MongoDB is not required to install or test this milestone. It will later have two separate roles:

1. **Auditor metadata database** — stores audit run summaries and findings.
2. **Target/staging application database** — used only for explicitly enabled, bounded, read-only preflight checks.

Start a disposable local instance when needed:

```powershell
Copy-Item .env.example .env
docker compose up -d mongo
docker compose ps
```

The compose service exposes MongoDB only on `127.0.0.1:27017`. Stop it with `docker compose down`; add `--volumes` only when you deliberately want to discard its local data. Do not use production credentials or production data for development/tests.

`.env` is ignored by Git. Its values are placeholders only; do not put a real connection string, password, access token, or application data in source control, test fixtures, logs, or issue reports.

## Scope decisions

### Planned supported inputs

The first usable analyzer will support one narrow, documented migration format plus declarative MongoDB schema/index manifests. The exact framework/format will be selected and documented before Milestone 3. Support will expand through explicit adapters, not by evaluating arbitrary code.

### Planned checks

- Validator tightening and document-shape compatibility risks
- Field removal/rename and BSON type-change risks
- Unique-index and index-operational risks
- Destructive operations, unbounded backfills, and recognizable retry/idempotency concerns
- Baseline drift for the supported declarative subset
- Explicit manual-review findings for dynamic or unsupported operations

### Explicit non-goals

- Guaranteeing a migration is safe or predicting exact production impact from source alone
- Executing arbitrary migration scripts during static analysis
- Supporting every language, migration framework, MongoDB topology, or server version in the first release
- Replacing backups, restore testing, deployment orchestration, monitoring, or human review
- Making blocking decisions solely with AI/LLM output

## Safety, privacy, and threat boundaries

- Static analysis must work offline and without a MongoDB connection.
- Any future target-database preflight is opt-in, read-only, least-privilege, time-bounded, and scan-limited.
- The tool must not log connection strings, passwords, tokens, or full application documents.
- Incomplete evidence must be represented as unknown/manual review, never as a safety guarantee.
- Audit metadata will avoid credentials and raw application documents by default.
- CI must run offline checks without database secrets; pull-request code must not receive production credentials by default.

## Architecture (planned)

```text
changed migration files + baseline/config
                |
             discover
                |
              parse  ----> unsupported/dynamic input -> explicit review result
                |
             compare
                |
             analyze
             /       \
  local reports       optional, read-only staging preflight
       |                         |
       +-----------> policy gate + optional audit-metadata store
```

See [docs/architecture.md](docs/architecture.md) for component responsibilities and the trust boundary.

## Repository layout

```text
src/migration_auditor/  Importable package (future parser, rules, reports, and CLI)
tests/                  Automated tests
docs/                   Architecture and decision records
.github/workflows/      Pull request and push CI
docker-compose.yml      Optional local MongoDB service
.env.example            Safe configuration placeholders
```

## Quality checks and CI

Run the same checks as CI:

```powershell
python -m ruff check .
python -m pytest
```

[.github/workflows/ci.yml](.github/workflows/ci.yml) runs both commands on pushes and pull requests across Python 3.11–3.14. It does not need MongoDB or secrets.

## Roadmap

- **Milestone 1:** Repository, scope, reproducible setup — complete locally; publication evidence pending.
- **Milestone 2:** Typed configuration, finding/run models, optional MongoDB audit store.
- **Milestone 3:** First CLI vertical slice using one documented input format.
- **Milestones 4–10:** Rule catalogue, baseline analysis, read-only preflight, reporting/policy, CI integration, evaluation, and handover.

See the project master plan for the full acceptance criteria. Claims in this README reflect implemented work only.

## Contributing

Keep changes small and testable. Before opening a pull request, install the dev dependencies and run the quality checks above. New analyzer behavior must include fixtures/tests and document both supported syntax and known limitations.
