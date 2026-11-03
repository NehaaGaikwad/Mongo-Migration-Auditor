# Repeatable local demo

Run these commands from the repository root after installing with `python -m pip install -e ".[dev]"`.

```powershell
# Safe analysis: expected exit 0
python -m migration_auditor check --config examples/milestone_4/configs/safe.toml --format json

# Baseline drift: expected policy exit 1
python -m migration_auditor check --config examples/milestone_5/migration-auditor.toml --format json

# Master-plan B01-B10 synthetic benchmark
python scripts/run_benchmark.py

# Quality checks
python -m pytest
python -m ruff check .
Set-Location frontend
npm.cmd run build
```

The Milestone 5 command intentionally reports risks; its non-zero policy exit is an
expected demonstration result. `migration-auditor history` requires an explicitly
configured dedicated audit metadata database. Optional target preflight requires a
separate read-only staging identity.
