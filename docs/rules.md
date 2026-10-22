# Implemented rules

The rules below are implemented in Milestone 4. They analyze only documented Manifest v1 declarations; they do not execute a migration or inspect database contents. A finding identifies a risk to validate, not proof that the migration will fail.

## MONGO-INDEX-001

**Default severity:** High. `create_index` with `unique: true` may fail on duplicate existing keys. Run a bounded duplicate-key check on staging.

## MONGO-INDEX-002

**Default severity:** Medium. `create_index` with `build_context: "high_write"` declares that the target receives high write traffic. Validate capacity, server-version behavior, and a deployment window on staging. The analyzer cannot infer write traffic itself.

## MONGO-SCHEMA-001

**Default severity:** High. `update_validator` with non-empty `added_required` can reject existing documents that lack a newly required field. Backfill and validate documents before enforcing it.

## MONGO-SCHEMA-002

**Default severity:** High. `remove_field` or `rename_field` with `compatibility_window: false` can break older application versions. Use a dual-read/dual-write or retirement window first.

## MONGO-SCHEMA-003

**Default severity:** High. `change_field_type` with `conversion_plan: false` can be incompatible with existing BSON values. Define, test, and run a bounded conversion before enforcement.

## MONGO-DATA-001

**Default severity:** High. `backfill` that declares none of `batched`, `checkpointed`, or `throttled` has no declared execution bounds. Add bounded batches and resume or rate-control safeguards.

## MONGO-DATA-002

**Default severity:** High. `drop_collection` and `delete_many` are destructive. Confirm a tested backup and recovery plan before deployment. The declared `recovery_plan` on `delete_many` is recorded for manifest validity but does not make deletion non-destructive.

## MONGO-EXEC-001

**Default severity:** Medium. `increment` with `idempotent: false` can apply more than once when retried. Use an idempotency key or retry-safe update strategy.

## MONGO-ANALYSIS-001

**Default severity:** Info. An operation outside the documented manifest subset, or malformed operation data, could not be analyzed. It is always emitted even if rule filtering is configured, because unsupported input must not be silently treated as safe. Its severity may be overridden but it cannot be disabled by ordinary rule filters.

## Limits and false-positive boundaries

- Declarations such as `conversion_plan`, `compatibility_window`, and `idempotent` are not independently verified. They describe the migration plan and do not replace review or staging checks.
- `build_context` is supplied by the manifest; the analyzer cannot derive workload, collection size, server version, lock behavior, or deployment topology.
- No baseline comparison or database preflight exists yet. The analyzer cannot prove validator compatibility, duplicate values, backfill cardinality, or recovery readiness.
- Arbitrary filters, raw update expressions, scripts, and migration-framework code remain unsupported and intentionally produce `MONGO-ANALYSIS-001` rather than a safety claim.

## MONGO-INDEX-003

**Default severity:** Medium. `drop_index` / `dropIndex` can alter query plans and affect query performance. Review dependent query plans and rollout capacity before removing the index.
