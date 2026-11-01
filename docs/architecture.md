# Architecture and scope decisions

## Current state

Milestones 1–5 are implemented: the package foundation, strict TOML configuration, stable serialized finding/run models, optional MongoDB audit metadata persistence, an offline manifest parser, deterministic Manifest v1 risk rules, baseline-aware migration analysis, sequential state-transition diffs, and CLI reports. MongoDB preflight remains planned.

## Intended components

| Component | Responsibility | Trust boundary |
| --- | --- | --- |
| Configuration | Parses strict TOML input paths, baseline path, policy, rules, and optional audit-store settings. | Rejects unknown/ambiguous fields; URI is excluded from object representations. |
| Parser | Converts Manifest v1 (`*.migration.json`) into normalized operations. | Implemented; does not execute migration code. Unsupported/dynamic input becomes an explicit result. |
| Comparison | Compares normalized operations to a declared schema/index baseline (`schema-baseline.json`). | Implemented; does not invent missing state. Unrepresented collections receive explicit `Confidence.UNKNOWN` rather than false safety. |
| Rule engine | Applies deterministic, versioned rules and emits evidence/remediation. | Implemented for documented index, validator, field compatibility/type, destructive, backfill-bound, retry-idempotency, baseline drift (`MONGO-BASELINE-001`), and unsupported operations. |
| Reporter and policy gate | Produces terminal/JSON reports with baseline diffs and applies configured exit policy. | Implemented; exit 1 is a policy finding and exit 2 is an analyzer/config error. Severity and confidence remain separate. |
| Optional preflight | Runs bounded, read-only checks on an explicitly configured staging target. | Implemented; disabled by default; separate least-privilege credentials. |
| Audit store | Persists sanitized run metadata and findings. | Implemented as opt-in MongoDB/Offline stores; separate from target application database and excludes secrets/raw documents by default. |

## Decisions recorded for Milestone 1

- **Language/package manager:** Python packaging via `pyproject.toml` and `pip`; Python 3.11+ is supported.
- **CLI library:** `argparse` chosen for minimal footprint and deterministic CI execution.
- **Data model approach:** Dataclasses used to normalize contracts while keeping dependencies minimal.
- **Runtime dependencies:** None required for offline static analysis.
- **Audit metadata persistence:** `pymongo==4.10.1`; disabled unless `audit_store.uri` is configured.
- **Local database:** Docker Compose starts a localhost-only MongoDB 8.0 service for later disposable development/integration testing.
- **License:** MIT, selected for simple permissive reuse.

## Decisions recorded for Milestone 5

- **Baseline format:** Declarative JSON schema (`schema-baseline.json`) specifying collections, indexes, and validator `$jsonSchema`.
- **Diff & state engine:** `BaselineStateTracker` traces operations sequentially, computes deterministic `BaselineDiff` structures, and detects state drift.
- **Explicit uncertainty:** Collections omitted from baseline receive explicit `Confidence.UNKNOWN` alerts rather than assuming safety.

## MongoDB preflight (Milestone 6)

Preflight is disabled unless both `[preflight].uri` and `database` are set. It connects
to the configured target database, separately from `[audit_store]`, with connection,
server selection, and query timeouts. Its implementation uses `ping`, `aggregate`,
`find`, and cursor iteration only. It never invokes a write, index, collection, or
validator mutation API. Use a separate database account granted only `find` on the
target database (MongoDB built-in `read` role), not `readWrite` or `dbOwner`; the
application's read-only intent alone does not constrain database permissions.

For example, as a database administrator in `mongosh`, create a dedicated user on
the staging database (supply its password interactively or through a secret manager):

```javascript
use app_staging
db.createUser({ user: "migration_auditor_ro", pwd: passwordPrompt(), roles: [{ role: "read", db: "app_staging" }] })
```

Use that account only in the protected preflight URI. The built-in `read` role grants
read access to collections and is the permission boundary; verify the role against
your MongoDB version and deployment policy before connecting.

Duplicate scans group the bounded first `max_documents` documents by unique key and
return only counts and field names, never key values. Results with no duplicate in
that bounded prefix are marked `sampled`; they do not certify the entire collection.
Validator checks support `$jsonSchema` required fields and scalar BSON types, scan a
bounded prefix, and omit document values. Unsupported syntax is `unknown`; connection,
permission, and query errors are `incomplete`. Incomplete/unknown checks cause the CLI
to return its analysis-error status. Scan and sample limits can miss later violations.

Example configuration:

```toml
[preflight]
uri = "mongodb://readonly-user:secret@localhost:27017/?authSource=admin"
database = "app_staging"
connect_timeout_ms = 5000
server_selection_timeout_ms = 5000
query_timeout_ms = 10000
max_documents = 10000
max_duplicate_groups = 10
```

Keep this file out of source control and protect it as a secret. Run using
`migration-auditor check --config migration-auditor.toml` or add `--format json`.
For a disposable local integration target, start `docker compose up -d mongo`, create
synthetic collections/fixtures using a separate setup script or `mongosh`, configure
the URI/database above, run the CLI, then remove only the disposable database and stop
the service. Unit tests use mocked MongoDB calls; they are not evidence of live-server
compatibility. The preflight module itself contains no mutation calls.
