# Database Schema Migration Auditor

A safety-focused CI/CD check for MongoDB migrations. The planned tool will inspect a deliberately small, documented subset of migration and schema/index inputs before deployment, identify known risks, and make uncertainty explicit.

> **Project status:** Milestone 3 — an offline CLI, one safe declarative migration format, deterministic first rules, text/JSON reports, and CI-compatible exit codes are implemented. Baseline comparison, MongoDB preflight, and broader migration-framework support remain planned.

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

The runtime dependency is pinned in [pyproject.toml](pyproject.toml): PyMongo is used only when audit metadata persistence is explicitly enabled. Development dependencies are pinned there too for repeatability.

## Configuration and audit metadata (Milestone 2)

Copy [migration-auditor.example.toml](migration-auditor.example.toml) to `migration-auditor.toml`. The typed configuration validates input paths, the optional baseline path, rule enable/disable lists, severity overrides, policy threshold, MongoDB metadata settings, timeouts, and retention before analysis starts.

```powershell
Copy-Item migration-auditor.example.toml migration-auditor.toml
```

The sample is intentionally offline: leaving `audit_store.uri` empty selects `OfflineAuditStore`, so analysis will never require MongoDB. When a metadata URI is supplied, `MongoAuditStore` connects to a **separate auditor metadata database**, creates a unique run-ID index, lookup indexes, and TTL retention indexes, and stores only run summaries plus sanitized findings.

Do not put a URI containing credentials in a committed TOML file. Supply it through an ignored local configuration mechanism or secret manager when a future CLI is added. Neither the configuration model nor connection errors expose the URI in normal error messages.

The stable finding contract includes rule ID, deterministic finding ID, severity, confidence, source location, collection/field/index where known, explanation, sanitized evidence, remediation, documentation link, and suppressibility. `AuditRun` records version, timestamps, status, a configuration fingerprint, and aggregate counts—never the configuration itself or credentials.

A deliberately sanitized example is available in [docs/sample-audit-record.json](docs/sample-audit-record.json). It is a schema illustration only; it contains no application documents or usable connection details.

### Optional disposable MongoDB integration test

Start the local service as described below, then run the opt-in test against it:

```powershell
docker compose up -d mongo
$env:MIGRATION_AUDITOR_TEST_MONGODB_URI = "mongodb://localhost:27017"
python -m pytest -m integration
Remove-Item Env:MIGRATION_AUDITOR_TEST_MONGODB_URI
```

The test creates a randomly named database and drops only that generated database during cleanup. Regular `python -m pytest` skips this integration test when the environment variable is absent.

## CLI and supported migration manifests (Milestone 3)

The first vertical slice intentionally supports only UTF-8 JSON files ending in `.migration.json`. The auditor reads data only: it never imports, runs, or evaluates JavaScript, Python, shell commands, or database migrations.

```powershell
migration-auditor version
migration-auditor rules
migration-auditor check --config migration-auditor.toml
migration-auditor check --config migration-auditor.toml --format json
```

`python -m migration_auditor` is an equivalent entry point. The `check` command discovers `*.migration.json` files recursively below `inputs.migration_paths` and is fully offline. An invalid config or malformed manifest returns exit code `2`; a recognized finding meeting `policy.fail_on` returns `1`; otherwise the command returns `0`.

### Manifest v1

Every manifest has exactly `version`, `collection`, and `operations` keys:

```json
{
  "version": 1,
  "collection": "users",
  "operations": [
    {
      "op": "create_index",
      "keys": {"email": 1},
      "unique": true,
      "name": "email_unique"
    }
  ]
}
```

Supported operations are deliberately limited:

| Operation | Accepted fields | Current behavior |
| --- | --- | --- |
| `create_index` | `op`, non-empty `keys` (each direction `1` or `-1`), optional boolean `unique`, optional `name` | `unique: true` emits `MONGO-INDEX-001`. |
| `drop_collection` | `op` only | Emits `MONGO-DATA-002`. |

Any unknown operation, dynamic operation, non-object operation, or unsupported field emits `MONGO-ANALYSIS-001`; it is never silently treated as safe. See [docs/rules.md](docs/rules.md) for rule rationale and remediation.

### Demo fixtures

[examples/milestone_3](examples/milestone_3) contains a safe index, a blocking unique-index risk, and an unsupported dynamic operation. [warning.toml](examples/milestone_3/configs/warning.toml) checks the same unique-index fixture with a Critical-only policy, demonstrating a visible but non-blocking warning:

```powershell
Set-Location examples/milestone_3
migration-auditor check --config migration-auditor.toml
# Produces MONGO-INDEX-001 and MONGO-ANALYSIS-001; exits 1 at the default high threshold.
migration-auditor check --config configs/warning.toml
# Produces MONGO-INDEX-001 but exits 0 because the configured threshold is Critical.
Set-Location ../..
```

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

### Supported inputs today

Manifest v1 (`*.migration.json`) is the sole supported migration input. Future formats will be added through explicit adapters, not arbitrary-code evaluation.

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
src/migration_auditor/  Importable package, parser, rules, reports, and CLI
tests/                  Automated tests
examples/milestone_3/   Reproducible CLI demonstration fixtures
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
- **Milestone 2:** Typed configuration, finding/run models, optional MongoDB audit store — complete locally; disposable-MongoDB evidence is opt-in.
- **Milestone 3:** First CLI vertical slice using one documented input format — complete locally.
- **Milestones 4–10:** Rule catalogue, baseline analysis, read-only preflight, reporting/policy, CI integration, evaluation, and handover.

See the project master plan for the full acceptance criteria. Claims in this README reflect implemented work only.

## Contributing

Keep changes small and testable. Before opening a pull request, install the dev dependencies and run the quality checks above. New analyzer behavior must include fixtures/tests and document both supported syntax and known limitations.
