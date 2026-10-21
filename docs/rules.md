# Implemented rules

Only the rules below are implemented in Milestone 3. All other catalogue entries remain planned and must not be assumed to run.

## MONGO-INDEX-001

**Default severity:** High. A `create_index` operation with `unique: true` can fail if existing documents contain duplicate keys. Static analysis cannot prove the data state, so the confidence is Medium. Run a bounded duplicate-key check on staging before deployment.

## MONGO-DATA-002

**Default severity:** High. `drop_collection` is a recognized destructive operation. Confirm a tested backup and recovery plan before deployment.

## MONGO-ANALYSIS-001

**Default severity:** Info. An operation outside the documented manifest subset, or malformed operation data, could not be analyzed. This rule is always emitted even if rule filtering is configured, because unsupported input must not be silently treated as safe. Review the operation manually or rewrite it using supported syntax.
