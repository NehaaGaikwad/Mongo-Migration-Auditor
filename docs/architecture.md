# Architecture and scope decisions

## Current state

Milestones 1 and 2 are implemented: the package foundation, strict TOML configuration, stable serialized finding/run models, and optional MongoDB audit metadata persistence. Parsing, comparison, rules, reporting, and preflight remain planned.

## Intended components

| Component | Responsibility | Trust boundary |
| --- | --- | --- |
| Configuration | Parses strict TOML input paths, policy, rules, and optional audit-store settings. | Rejects unknown/ambiguous fields; URI is excluded from object representations. |
| Parser | Converts one supported, declarative input format into normalized operations. | Does not execute migration code. Unsupported/dynamic input becomes an explicit result. |
| Comparison | Compares normalized operations to a declared schema/index baseline. | Does not invent missing state. |
| Rule engine | Applies deterministic, versioned rules and emits evidence/remediation. | No model/AI output is a blocking authority. |
| Reporter and policy gate | Produces terminal/JSON reports and applies configured exit policy. | Severity and confidence remain separate. |
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
