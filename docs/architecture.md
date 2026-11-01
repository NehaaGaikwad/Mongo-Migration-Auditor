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
| Optional preflight | Runs bounded, read-only checks on a staging target. | Planned (Milestone 6); disabled by default; separate least-privilege credentials. |
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
