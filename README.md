# Database Schema Migration Auditor

A safety-focused CI/CD check for MongoDB migrations. The planned tool will inspect a deliberately small, documented subset of migration and schema/index inputs before deployment, identify known risks, and make uncertainty explicit.

> **Project status:** Milestone 6 — the offline CLI supports baseline-aware analysis, and optional bounded MongoDB preflight checks are available for explicitly configured staging databases.

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

The runtime dependency is pinned in [pyproject.toml](pyproject.toml): PyMongo is used only when optional audit metadata persistence or explicitly configured read-only preflight is enabled. Development dependencies are pinned there too for repeatability.

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
`policy_failed` status when a finding meets the configured policy threshold (High by
default). Malformed manifests
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
   The committed Vercel configuration pins the build command and `dist` output
   directory. Add build environment variable `AUDITOR_API_URL` with the public Render API URL, such as
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

The audit-store integration test still uses `MIGRATION_AUDITOR_TEST_MONGODB_URI`.
The preflight integration test uses two separate environment variables:

- `MIGRATION_AUDITOR_TEST_MONGODB_FIXTURE_URI`: write-capable test-only identity for inserting synthetic fixtures and dropping the generated database.
- `MIGRATION_AUDITOR_TEST_MONGODB_TARGET_URI`: read-only identity used by preflight and checked with a denied insert attempt.

Both URIs must point to the same disposable test cluster. Because the database name
is randomly generated on each run, the fixture identity needs database-creation and
drop permissions on that test cluster, while the target identity needs read access
to the generated database. Grant the target identity a read-only role such as
`readAnyDatabase` only on a dedicated test cluster. Never use these broad test roles
or credentials on production. Inject the values through a protected environment or
secret manager rather than placing credentials in the repository or shell command
history. Then run:

```powershell
python -m pytest -m integration
Remove-Item Env:MIGRATION_AUDITOR_TEST_MONGODB_FIXTURE_URI
Remove-Item Env:MIGRATION_AUDITOR_TEST_MONGODB_TARGET_URI
```

The preflight test inserts fixtures through the fixture identity, verifies the target
identity receives MongoDB authorization code 13 on an insert attempt, then runs
preflight through that read-only target identity. Cleanup drops only the generated
database through the fixture identity. Regular `python -m pytest` skips the live
preflight test when either preflight URI is absent. Do not run it against production
or a cluster containing data that is not disposable.

## CLI and supported migration manifests (Milestone 4)

The first vertical slice intentionally supports only UTF-8 JSON files ending in `.migration.json`. The auditor reads data only: it never imports, runs, or evaluates JavaScript, Python, shell commands, or database migrations.

```powershell
migration-auditor version
migration-auditor rules
migration-auditor check --config migration-auditor.toml
migration-auditor check --config migration-auditor.toml --format json
migration-auditor history --config migration-auditor.toml --format json
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
| `create_index` | `op`, non-empty `keys` with directions `1`/`-1` or documented index types `2d`, `2dsphere`, `text`, `hashed`, `$**`; optional boolean `unique`, optional non-empty `name`, optional `build_context` (`unknown` or `high_write`) | `unique: true` emits `MONGO-INDEX-001`; `high_write` emits `MONGO-INDEX-002`. |
| `drop_collection` | `op` only | Emits `MONGO-DATA-002`. |
| `delete_many` | `op`, `filter_scope` (`all` or `filtered`), boolean `recovery_plan` | Emits `MONGO-DATA-002`. The `deleteMany` adapter accepts a JSON-object `filter` only to classify all-versus-filtered scope; it does not evaluate filter semantics. |
| `update_validator` | `op`, `added_required` array of unique non-empty fields | Each added field emits `MONGO-SCHEMA-001`. |
| `remove_field` | `op`, `field`, boolean `compatibility_window` | Emits `MONGO-SCHEMA-002` when no compatibility window is declared. |
| `rename_field` | `op`, `from`, `to`, boolean `compatibility_window` | Emits `MONGO-SCHEMA-002` when no compatibility window is declared. |
| `change_field_type` | `op`, `field`, `from_type`, `to_type`, boolean `conversion_plan` | Emits `MONGO-SCHEMA-003` without a declared conversion plan. |
| `backfill` | `op`, `field`, boolean `batched`, `checkpointed`, `throttled` | Emits `MONGO-DATA-001` when no execution bound is declared. |
| `increment` | `op`, `field`, boolean `idempotent` | Emits `MONGO-EXEC-001` when retries are not idempotent. |

Any unknown operation, dynamic operation, non-object operation, or unsupported field emits `MONGO-ANALYSIS-001`; it is never silently treated as safe. See [docs/rules.md](docs/rules.md) for rule rationale and remediation.

`MONGO-ANALYSIS-001` is also used for malformed declarations, but reports distinguish
them: `unsupported_operations_count` counts unknown/dynamic operations, while
`malformed_operations_count` counts recognized operation names with invalid,
ambiguous, or unsafe fields. Both are fail-closed findings and cause a policy failure
regardless of the configured severity threshold; neither is a policy pass.

## Reporting, policy, and audit history (Milestone 7)

The `check` command returns stable text or JSON reports with file/operation context,
rule ID, severity, confidence, evidence, remediation, and documentation links. Policy
thresholds remain configured through `policy.fail_on`; unsupported or malformed input
is always fail-closed.

When `[audit_store].uri` is explicitly configured, each CLI check persists a sanitized
run summary and its findings to the dedicated audit metadata database. JSON reports
include a `persistence` status of `persisted`, `offline`, or `error`; persistence
failure returns the analyzer-error exit code rather than silently claiming a recorded
audit. `migration-auditor history --config migration-auditor.toml` lists recent
sanitized run summaries (up to 100), while adding `--by-rule` returns recurring
finding counts by rule and severity. Neither query returns connection credentials,
raw configuration, or application documents.

### Narrow, auditable suppressions

Use `[[suppressions]]` only for a specific `finding_id` emitted by a prior report.
Each entry requires a non-empty change/review reason and ISO-8601 `expires_on` date.
Suppressed findings remain in `suppressed_findings` in the JSON report and persisted
run record, including the reason and expiry, but do not affect the policy threshold.
Expired entries do nothing. Unsupported or malformed input is never suppressible.

```toml
[[suppressions]]
finding_id = "0123456789abcdef01234567"
reason = "Approved rollout exception CAB-123"
expires_on = "2027-01-31"
```

### Practical MongoDB JSON aliases

The declarative JSON adapter also accepts common MongoDB-style aliases and converts
them into the same canonical internal operations used by the CLI rules. It does not
execute code, connect to MongoDB, or evaluate expressions. Both spellings below are
supported only when the documented fields can be validated unambiguously:

| Practical declaration | Canonical operation | Result |
| --- | --- | --- |
| `createIndex` with `keys` and `options.unique`, `options.name`, and/or `options.buildContext`; or `create_index` with `write_rate: "high"` | `create_index` | Unique-index and high-write risks are detected. Compound numeric `1`/`-1` keys are supported. `write_rate` only accepts `"high"`, which maps to `high_write`. |
| `dropIndex` with `name` | `drop_index` | `MONGO-INDEX-003` warns of query-performance impact. |
| `createCollection`, `addField` with `field`/`required` | `create_collection`, `add_field` | Recognized; an immediately required field emits the validator rule. |
| `deleteMany` with a JSON-object `filter` | `delete_many` | Empty filters mean `all`; every delete is reported as destructive. |
| `updateMany`/`bulkUpdate` with JSON-object `filter` and non-empty `$set` and/or `$unset` objects | `bulk_update` | An absent batch/checkpoint/throttle declaration emits the execution-bounds rule. |
| `backfill` with `batchSize`, `checkpoint`, or `throttleMs` (top-level or in its `options`) | `backfill` | Positive numeric bounds normalize to declared safeguards. |
| `updateValidator` with `{ "validator": { "$jsonSchema": { "required": [...] } } }` | `update_validator` | Newly required fields emit the validator rule. |

The original snake_case format remains fully compatible. For all aliases,
`buildContext`, `compatibilityWindow`, `conversionPlan`, `filterScope`, and
`recoveryPlan` are accepted as camelCase equivalents. Raw driver code, JavaScript or
Python migration files, aggregation pipelines, arbitrary update operators, index
options such as `partialFilterExpression`, and framework-specific source files are
not implemented adapters: source files are rejected by upload/discovery and dynamic
JSON operations produce an unsupported-operation finding. This intentional
fail-closed behavior is not a claim that the migration is safe.

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

### Optional read-only preflight

Set `[preflight].uri` and `[preflight].database` in a protected TOML config to enable
checks; ordinary analysis remains offline if these are omitted. Use a dedicated MongoDB
account with the built-in `read` role on the target database. The tool does not make
the connection read-only by itself: MongoDB permissions enforce that boundary. Never
reuse the audit metadata database credentials for the target database.

Preflight checks duplicates for declared unique indexes and checks `$jsonSchema`
required fields and supported scalar types for validator updates. Scans are bounded by
`max_documents`, duplicate evidence by `max_duplicate_groups`, and query execution by
`query_timeout_ms`. Reports label results `sampled`, `incomplete`, `unknown`, or
`skipped`; sampled success does not mean the full collection was checked. See
[`docs/architecture.md`](docs/architecture.md#mongodb-preflight) for the TOML example,
least-privilege role guidance, and disposable integration procedure.

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

The workflow also proves offline CI gate behavior for safe, warning, blocking, and
analyzer-error fixtures. Optional staging preflight is a manually dispatched protected
workflow; see [CI/CD integration](docs/ci-cd.md). It is never enabled for pull
requests.

For reviewer handoff, use the [repeatable demo](docs/demo.md),
[troubleshooting guide](docs/troubleshooting.md), and
[final implementation report](docs/final-report.md).

## Roadmap

- **Milestone 1:** Repository, scope, reproducible setup — complete locally; publication evidence pending.
- **Milestone 2:** Typed configuration, finding/run models, optional MongoDB audit store — complete locally; disposable-MongoDB evidence is opt-in.
- **Milestone 3:** First CLI vertical slice using one documented input format — complete locally.
- **Milestone 4:** Initial deterministic rule catalogue for Manifest v1 — complete locally.
- **Milestone 5:** Baseline-aware migration analysis, diff engine, and sequential drift detection — complete locally.
- **Milestone 6:** Read-only, bounded MongoDB preflight — complete locally.
- **Milestone 7:** Reporting, policy, audited suppressions, and MongoDB audit history — complete locally.
- **Milestone 8:** CI/CD integration — in progress locally; requires a pushed GitHub Actions run for completion evidence.
- **Milestone 9:** Evaluation and hardening — in progress locally; see [evaluation evidence](docs/evaluation.md).
- **Milestone 10:** Documentation, demo, and handover — complete locally; release tag and remote CI evidence remain pending a push.

See the project master plan for the full acceptance criteria. Claims in this README reflect implemented work only.

## Contributing

Keep changes small and testable. Before opening a pull request, install the dev dependencies and run the quality checks above. New analyzer behavior must include fixtures/tests and document both supported syntax and known limitations.
