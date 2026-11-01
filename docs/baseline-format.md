# Schema Baseline Format and Migration Diff Engine (Milestone 5)

## Overview

The Database Schema Migration Auditor supports baseline-aware migration analysis. Rather than evaluating migration files in total isolation, the auditor compares proposed changes against a declared declarative baseline state (`schema-baseline.json`), validates state transitions in multi-migration sequences, and identifies baseline drift or contradictory transitions (`MONGO-BASELINE-001`).

When a collection is not declared in the baseline, the tool does **not** assume safety; it explicitly records `Confidence.UNKNOWN` and alerts the user to incomplete baseline coverage.

## Baseline Schema (`schema-baseline.json`)

The baseline file must be UTF-8 encoded JSON containing a `version` integer (currently `1`) and a `collections` mapping or array.

### Specification

```json
{
  "version": 1,
  "collections": {
    "users": {
      "validator": {
        "$jsonSchema": {
          "bsonType": "object",
          "required": ["email", "status"],
          "properties": {
            "email": {"bsonType": "string"},
            "status": {"bsonType": "string"},
            "age": {"bsonType": "int"}
          }
        }
      },
      "indexes": [
        {
          "name": "_id_",
          "keys": {"_id": 1}
        },
        {
          "name": "email_unique",
          "keys": {"email": 1},
          "unique": true
        }
      ]
    }
  }
}
```

### Supported Elements

| Element | Description | Validation |
| --- | --- | --- |
| `version` | Baseline format version | Must be integer `1`. |
| `collections` | Map of collection names to baseline definitions (or list with `"name"`) | Non-empty string keys. |
| `validator.$jsonSchema` | MongoDB JSON Schema validator definition | Extracts top-level `required` fields and top-level `properties` scalar BSON types. |
| `indexes` | List of established index specifications | Each entry requires non-empty `keys` mapping with directions (`1`, `-1`) or special types (`2d`, `2dsphere`, `text`, `hashed`, `$**`). Key ordering in compound indexes is preserved. |

## Diff Engine & Sequential Transition Tracking

The diff engine (`BaselineStateTracker`) executes the normalized operations in sequential file and operation order:

1. **Deterministic Diffs (`BaselineDiff`):**
   - For each collection touched by migrations, tracks `collections_created`, `collections_dropped`, `indexes_added`, `indexes_dropped`, `fields_added`, `fields_removed`, `fields_renamed`, `type_changes`, and `validator_changes`.
2. **Sequential Ordering:**
   - Changes applied in earlier migration files (e.g., `001_add_index.migration.json`) persist in the state tracker and are visible to subsequent migration files (e.g., `002_drop_index.migration.json`).
3. **Contradiction Detection (`MONGO-BASELINE-001`):**
   - **Duplicate Index:** Creating an index whose keys (in exact order) or name already exist in baseline or were created in prior migrations.
   - **Missing Index Drop:** Attempting to drop an index that does not exist.
   - **Duplicate/Missing Collection:** Dropping a non-existent collection or creating an existing one.
   - **Schema Drift:** Removing/renaming non-existent fields, creating naming collisions, or conflicting with declared baseline BSON field types.
4. **Explicit Uncertainty:**
   - If a migration references an undeclared collection, the engine sets `confidence = Confidence.UNKNOWN` on the diff and emits an explicit `MONGO-BASELINE-001` informational finding. It never emits a false safety guarantee for untracked collections.

## Configuration

In `migration-auditor.toml`:

```toml
[inputs]
migration_paths = ["migrations"]
baseline_path = "schema-baseline.json"

[policy]
fail_on = "high"
```

## Limitations

- Manifest v1 currently supports top-level required fields and scalar property types in schema baselines and validator updates. Deep analysis of nested BSON document validators, array items, and complex validation expressions is deferred to future refinement.
- The baseline engine validates declarative manifests only. Dynamic JavaScript or runtime calculations are not executed.
- The baseline assumes single-branch linear migration history.
- Live database schema inspection is deferred to Milestone 6 (read-only preflight).
