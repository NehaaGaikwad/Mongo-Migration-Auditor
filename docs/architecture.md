# Architecture and scope decisions

## Current state

Only the repository foundation exists in Milestone 1. The diagram below describes the intended architecture; it is not a claim that the components have been implemented.

## Intended components

| Component | Responsibility | Trust boundary |
| --- | --- | --- |
| Discovery | Finds configured migration files, config, and baseline inputs. | Reads only the checked-out repository. |
| Parser | Converts one supported, declarative input format into normalized operations. | Does not execute migration code. Unsupported/dynamic input becomes an explicit result. |
| Comparison | Compares normalized operations to a declared schema/index baseline. | Does not invent missing state. |
| Rule engine | Applies deterministic, versioned rules and emits evidence/remediation. | No model/AI output is a blocking authority. |
| Reporter and policy gate | Produces terminal/JSON reports and applies configured exit policy. | Severity and confidence remain separate. |
| Optional preflight | Runs bounded, read-only checks on a staging target. | Disabled by default; separate least-privilege credentials. |
| Audit store | Persists sanitized run metadata and findings. | Separate from target application database; excludes secrets/raw documents by default. |

## Decisions recorded for Milestone 1

- **Language/package manager:** Python packaging via `pyproject.toml` and `pip`; Python 3.11+ is supported.
- **CLI library:** Deferred until Milestone 3 because there is no user-facing command yet.
- **Data model approach:** Deferred until Milestone 2; use the least complex typed representation that preserves stable contracts.
- **Runtime dependencies:** None until a feature requires one. This keeps the bootstrap testable without a database.
- **Local database:** Docker Compose starts a localhost-only MongoDB 8.0 service for later disposable development/integration testing.
- **License:** MIT, selected for simple permissive reuse.

## Deferred operational decisions

The input migration format, baseline syntax, MongoDB driver/persistence configuration, rule IDs/severities, and CI failure policy all require implementation evidence and are intentionally deferred to their assigned milestones.
