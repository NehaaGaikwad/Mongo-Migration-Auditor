# CI/CD integration

## Pull-request checks

The standard [`CI`](../.github/workflows/ci.yml) workflow has no database
credentials. It runs lint and tests across supported Python versions, then runs an
offline auditor gate that proves four CI outcomes:

- a safe manifest passes (exit `0`);
- a visible High finding remains non-blocking under a Critical threshold (exit `0`);
- a High finding blocks under the default High threshold (exit `1`);
- malformed input is an analyzer error, not a policy finding (exit `2`).

This workflow deliberately does not run MongoDB preflight and does not receive
production or staging secrets on pull requests.

## Reusable workflow

[`Reusable migration auditor check`](../.github/workflows/auditor-check.yml) accepts
a repository-relative config path, Python version, and a pinned published auditor
repository/ref. It checks out the caller and auditor separately, runs the offline
policy gate, and uploads the JSON report even when the gate fails. A caller template is in
[`docs/examples/reusable-auditor-workflow.yml`](examples/reusable-auditor-workflow.yml).
After publishing a reviewed release, callers must pin `OWNER/REPOSITORY` to that tag
or an immutable commit SHA. Do not add preflight secrets to this reusable PR workflow.

## Optional protected staging preflight

[`Staging MongoDB preflight`](../.github/workflows/staging-preflight.yml) is manually
dispatched only. Before enabling it, create a GitHub Environment named
`staging-preflight`, require an appropriate reviewer, and store the complete TOML
configuration in its `MIGRATION_AUDITOR_PREFLIGHT_CONFIG` secret. The config must use
a dedicated read-only database identity, bounded `max_documents` and query timeouts,
and repository-relative migration paths. Never place a production URI in repository
files, pull-request secrets, workflow logs, or command output.

The preflight result exits `0` only when the analysis and bounded checks complete;
policy failures return `1`, and incomplete connectivity/permission/timeout checks
return `2`. Review the JSON report in the workflow log before deploying.
