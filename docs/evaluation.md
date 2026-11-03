# Milestone 9 evaluation and hardening

## Reproducible benchmark

Run the master-plan B01–B10 scenarios against this auditor with:

```powershell
python scripts/run_benchmark.py
```

The runner creates only temporary synthetic manifests and reports the expected rule,
observed rules, pass/fail status, and per-scenario runtime. It covers validator
tightening, compatibility breaks, type changes, unique/high-write indexes, unbounded
backfills, retry safety, index removal, unsupported input, and baseline drift.

`B04` is a static unique-index risk check; live duplicate evidence is separately
covered by the disposable MongoDB preflight integration test. `B08` reports the
documented missing query-usage limitation rather than claiming observed workload
impact.

## Competitor applicability

Source scope was rechecked on 2026-11-03. The following shortlist entries are not
applicable to the B01–B10 declarative MongoDB manifest corpus and are therefore not
counted as competitor failures:

| Tool | Applicability | Evidence |
| --- | --- | --- |
| safe-migrate | Not applicable: PostgreSQL SQL tool | [Repository README](https://github.com/dsecurity49/safe-migrate) describes PostgreSQL migration parsing and PostgreSQL 14–18 support. |
| Squawk | Not applicable: PostgreSQL SQL linter | [Repository README](https://github.com/sbdchd/squawk) identifies it as a Postgres migration/SQL linter. |
| Strong Migrations | Not applicable: Rails PostgreSQL/MySQL/MariaDB library | [Repository README](https://github.com/ankane/strong_migrations) lists those supported databases. |
| pg-migration-lint | Not applicable: PostgreSQL migration analyzer | [Repository README](https://github.com/robert-sjoblom/pg-migration-lint) describes PostgreSQL migration files. |
| migrate-mongo | Not applicable to this corpus: MongoDB JavaScript migration runner, not a declarative safety linter | [Repository README](https://github.com/seppevs/migrate-mongo) documents Node.js JavaScript migration files and `up`/`down` execution. |

No competitor result is claimed without a reproducible command, version, date, and
retained output. A future comparison must translate each scenario to a tool's native
format and label any semantic mismatch explicitly.

## Hardening evidence

- Manifests are limited by `inputs.max_manifest_bytes` (default 1 MB) before JSON
  parsing and `inputs.max_operations_per_manifest` (default 10,000) after parsing.
- The API independently retains upload/archive/file-size limits and ZIP traversal
  rejection.
- Unit tests cover malformed JSON/UTF-8/input, unsafe manifest operations, MongoDB
  connection/permission/query failures, bounded scans, resource limits, and
  compensating cleanup after an interrupted findings write.
- CI runs `python -m pip check` after each installation to detect missing or
  incompatible declared runtime dependencies.
- Disposable MongoDB integration tests must use separate fixture-write and
  preflight-read-only identities; never use production data or credentials.
- Preflight is skipped when static manifest parsing has errors, so malformed or
  oversized input cannot trigger a second unbounded read path.

## Known limitations

The benchmark does not establish production precision/recall: it is a deterministic
synthetic fixture suite, not a labelled customer dataset. MongoDB topology, workload,
server version, and dynamic migration code remain explicit limitations. Competitor
hands-on results remain pending until each applicable tool is run with its native
format and saved evidence.
