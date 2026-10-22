# Database Schema Migration Auditor

A safety-focused CI/CD check for MongoDB migrations. The planned tool will inspect a deliberately small, documented subset of migration and schema/index inputs before deployment, identify known risks, and make uncertainty explicit.

> **Project status:** Milestone 4 — the offline CLI now detects documented validator, field-compatibility, type-change, index-operational, destructive-operation, backfill-bound, and retry-idempotency risks in the safe declarative manifest subset. Baseline comparison, MongoDB preflight, and broader migration-framework support remain planned.

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
| Public repository URL | Complete: https://github.com/NehaaGaikwad/Mongo-Migration-Auditor |
| Remote CI run evidence | Pending first published CI run URL |

The project uses the MIT License; see [LICENSE](LICENSE). The prepared project backlog and completion criteria are in [docs/project-board.md](docs/project-board.md).

## Requirements

- Python 3.11 or newer (CI tests 3.11 through 3.14)
- Git
- Node.js 20+ (only for the web frontend)
- Docker Desktop only if you want a local MongoDB instance for later milestones

## Quick start

```powershell
git clone https://github.com/NehaaGaikwad/Mongo-Migration-Auditor.git
Set-Location Mongo-Migration-Auditor
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
python -m ruff check .
python -m pytest
```

On macOS/Linux, activate the environment with `source .venv/bin/activate`.

The runtime dependency is pinned in [pyproject.toml](pyproject.toml): PyMongo is used only when audit metadata persistence is explicitly enabled. Development dependencies are pinned there too for repeatability.

## Web application

The project also includes a connected web application: a static browser interface in
[`frontend/`](frontend) and a FastAPI backend in
[`src/migration_auditor/api.py`](src/migration_auditor/api.py). The browser uploads
manifest files to the API; the API writes them into a request-scoped temporary
directory, calls the existing `analyze()` package function, returns its real report,
and removes the directory before responding. No uploaded code is executed.

```powershell
# Terminal 1: from the repository root
python -m uvicorn migration_auditor.api:app --reload --port 8000

# Terminal 2: serve the static frontend from its directory
Set-Location frontend
python -m http.server 5173
```

Open `http://localhost:5173`. The interface accepts up to ten `.migration.json`
files, or a ZIP containing only those files; displays actual findings, errors,
unsupported-operation results and policy status; filters by severity and rule ID;
and downloads the returned JSON report. “Load risk sample” creates a local sample
file, but it is still submitted to and analyzed by the live backend.

### HTTP API

`GET /health` returns `{ "status": "ok", "version": "..." }` for host health checks.

`POST /api/audits` accepts multipart form field `files` and returns the existing
report contract plus `policy_failed`. A successful analysis is `200`, including a
`policy_failed` status when a high/critical finding is present. Malformed manifests
return `422` with the partial real report and errors; bad file types or unsafe ZIPs
return `400`; excessive input sizes/counts return `413`. The web API has no endpoint
for arbitrary paths, TOML configuration, executable commands, database access, or
saved uploads.

The upload limits are 10 files, 1 MB per manifest, 5 MB per ZIP and 10 MB expanded
archive content. ZIP members must be relative `.migration.json` files, have unique
names, and cannot traverse directories. These bounds mitigate, but do not replace,
hosting-level abuse controls such as rate limiting and request timeouts.

### Deploying

Deployment needs accounts with your hosting providers, so no live URLs are claimed
yet. The repository is ready for a Render backend and Vercel frontend:

1. Push these changes to GitHub. In Render, create a new **Blueprint** from the
   repository; it uses [`render.yaml`](render.yaml). Set `ALLOWED_ORIGINS` to the
   exact Vercel URL after the frontend is created (for example
   `https://your-project.vercel.app`). Render supplies `PORT`; do not set it.
2. In Vercel, import the same repository and set **Root Directory** to `frontend`.
   Its build command is `npm run build` and output directory is `dist`. Add build
   environment variable `AUDITOR_API_URL` with the public Render API URL, such as
   `https://mongo-migration-auditor-api.onrender.com`, then deploy.
3. Update Render's `ALLOWED_ORIGINS` with the Vercel production URL and redeploy the
   backend. Verify `https://<render-service>/health`, then upload
   `examples/milestone_4/migrations/002_risks.migration.json` in the deployed site.

`AUDITOR_API_URL` is compiled into the public static frontend and is not a secret.
Do not place credentials in it or in source control. If using a Vercel preview URL,
add that exact origin to `ALLOWED_ORIGINS` too.

## Configuration and audit metadata (Milestone 2)

Copy [migration-auditor.example.toml](migration-auditor.example.toml) to `migration-auditor.toml`. The typed configuration validates input paths, the optional baseline path, rule enable/disable lists, severity overrides, policy threshold, MongoDB metadata settings, timeouts, and retention before analysis starts. Rule filters and severity overrides must name an implemented rule ID; unknown or duplicate IDs are configuration errors.

```powershell
Copy-Item migration-auditor.example.toml migration-auditor.toml
```

The sample is intentionally offline: leaving `audit_store.uri` empty selects `OfflineAuditStore`, so analysis will never require MongoDB. When a metadata URI is supplied, `MongoAuditStore` connects to a **separate auditor metadata database**, creates a unique run-ID index, lookup indexes, and TTL retention indexes, and stores only run summaries plus sanitized findings.

Do not put a URI containing credentials in a committed TOML file. Supply it through an ignored local configuration mechanism or secret manager when a future CLI is added. Neither the configuration model nor connection errors expose the URI in normal error messages.

The stable finding contract includes rule ID, deterministic finding ID, severity, confidence, source location, collection/field/index where known, explanation, sanitized evidence, remediation, documentation link, and suppressibility. Finding IDs are a versioned hash of the rule ID, title, source location, collection/field/index context, and evidence. `AuditRun` records version, timestamps, status, a configuration fingerprint, and aggregate counts—never the configuration itself or credentials.

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

## CLI and supported migration manifests (Milestone 4)

The first vertical slice intentionally supports only UTF-8 JSON files ending in `.migration.json`. The auditor reads data only: it never imports, runs, or evaluates JavaScript, Python, shell commands, or database migrations.

```powershell
migration-auditor version
migration-auditor rules
migration-auditor check --config migration-auditor.toml
migration-auditor check --config migration-auditor.toml --format json
```

`python -m migration_auditor` is an equivalent entry point. The `check` command discovers `*.migration.json` files recursively below `inputs.migration_paths` and is fully offline. Every configured path must exist, explicit file paths must end in `.migration.json`, and at least one manifest must be discovered. An invalid config, empty/missing input, or malformed manifest returns exit code `2`; a recognized finding meeting `policy.fail_on` returns `1`; otherwise the command returns `0`.

### Manifest v1

Every manifest has exactly `version`, `collection`, and `operations` keys. `version` must be the integer `1` (not a string, float, or boolean); `collection` must be a non-empty string; `operations` must be an array.

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
| `create_index` | `op`, non-empty `keys` with directions `1` or `-1`, optional boolean `unique`, optional non-empty `name`, optional `build_context` (`unknown` or `high_write`) | `unique: true` emits `MONGO-INDEX-001`; `high_write` emits `MONGO-INDEX-002`. |
| `drop_collection` | `op` only | Emits `MONGO-DATA-002`. |
| `delete_many` | `op`, `filter_scope` (`all` or `filtered`), boolean `recovery_plan` | Emits `MONGO-DATA-002`. Raw filters are intentionally unsupported. |
| `update_validator` | `op`, `added_required` array of unique non-empty fields | Each added field emits `MONGO-SCHEMA-001`. |
| `remove_field` | `op`, `field`, boolean `compatibility_window` | Emits `MONGO-SCHEMA-002` when no compatibility window is declared. |
| `rename_field` | `op`, `from`, `to`, boolean `compatibility_window` | Emits `MONGO-SCHEMA-002` when no compatibility window is declared. |
| `change_field_type` | `op`, `field`, `from_type`, `to_type`, boolean `conversion_plan` | Emits `MONGO-SCHEMA-003` without a declared conversion plan. |
| `backfill` | `op`, `field`, boolean `batched`, `checkpointed`, `throttled` | Emits `MONGO-DATA-001` when no execution bound is declared. |
| `increment` | `op`, `field`, boolean `idempotent` | Emits `MONGO-EXEC-001` when retries are not idempotent. |

Any unknown operation, dynamic operation, non-object operation, or unsupported field emits `MONGO-ANALYSIS-001`; it is never silently treated as safe. See [docs/rules.md](docs/rules.md) for rule rationale and remediation.

### Demo fixtures

[examples/milestone_3](examples/milestone_3) contains the original CLI demonstration. [examples/milestone_4](examples/milestone_4) adds safe declarations and representative Milestone 4 risks. [warning.toml](examples/milestone_3/configs/warning.toml) checks the unique-index fixture with a Critical-only policy, demonstrating a visible but non-blocking warning:

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
examples/milestone_3/   Original reproducible CLI demonstration fixtures
examples/milestone_4/   Validator, compatibility, backfill, and retry-rule fixtures
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
- **Milestone 4:** Initial deterministic rule catalogue for Manifest v1 — complete locally.
- **Milestones 5–10:** Baseline analysis, read-only preflight, reporting/policy, CI integration, evaluation, and handover.

See the project master plan for the full acceptance criteria. Claims in this README reflect implemented work only.

## Contributing

Keep changes small and testable. Before opening a pull request, install the dev dependencies and run the quality checks above. New analyzer behavior must include fixtures/tests and document both supported syntax and known limitations.
