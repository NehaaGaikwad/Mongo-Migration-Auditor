# Architecture and scope decisions

## Current state

Milestones 1–3 are implemented: the package foundation, strict TOML configuration, stable serialized finding/run models, optional MongoDB audit metadata persistence, plus an offline manifest parser, deterministic rules, and CLI reports. Baseline comparison and preflight remain planned.

## Intended components

| Component | Responsibility | Trust boundary |
| --- | --- | --- |
| Configuration | Parses strict TOML input paths, policy, rules, and optional audit-store settings. | Rejects unknown/ambiguous fields; URI is excluded from object representations. |
| Parser | Converts Manifest v1 (`*.migration.json`) into normalized operations. | Implemented; does not execute migration code. Unsupported/dynamic input becomes an explicit result. |
| Comparison | Compares normalized operations to a declared schema/index baseline. | Does not invent missing state. |
| Rule engine | Applies deterministic, versioned rules and emits evidence/remediation. | Implemented initially for unique indexes, collection drops, and unsupported operations. No model/AI output is a blocking authority. |
| Reporter and policy gate | Produces terminal/JSON reports and applies configured exit policy. | Implemented; exit 1 is a policy finding and exit 2 is an analyzer/config error. Severity and confidence remain separate. |
| Optional preflight | Runs bounded, read-only checks on a staging target. | Disabled by default; separate least-privilege credentials. |
| Audit store | Persists sanitized run metadata and findings. | Implemented as opt-in MongoDB/Offline stores; separate from target application database and excludes secrets/raw documents by default. |

## Decisions recorded for Milestone 1

- **Language/package manager:** Python packaging via `pyproject.toml` and `pip`; Python 3.11+ is supported.
- **CLI library:** Deferred until Milestone 3 because there is no user-facing command yet.
- **Data model approach:** Deferred until Milestone 2; use the least complex typed representation that preserves stable contracts.
- **Runtime dependencies:** None until a feature requires one. This keeps the bootstrap testable without a database.
- **Audit metadata persistence:** `pymongo==4.10.1`; disabled unless `audit_store.uri` is configured. Runs and findings receive TTL retention indexes, and `run_id` is unique.
- **Local database:** Docker Compose starts a localhost-only MongoDB 8.0 service for later disposable development/integration testing.
- **License:** MIT, selected for simple permissive reuse.

## Deferred operational decisions

The input migration format, baseline syntax, MongoDB driver/persistence configuration, rule IDs/severities, and CI failure policy all require implementation evidence and are intentionally deferred to their assigned milestones.
