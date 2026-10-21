# Initial project board

This file is the source plan for the GitHub milestone/issue board. Once the repository is published, create the items below in GitHub and replace their placeholders with issue and milestone links. It avoids claiming that a remote board already exists.

## Milestone 1 — Repository, scope and reproducible setup

| Issue | Status | Evidence / close condition |
| --- | --- | --- |
| Publish repository and configure its URL | Pending remote creation | README links and GitHub Actions run URL are real and verified. |
| Verify CI on first push | Pending remote creation | All Python matrix jobs are green. |
| Record completion evidence | Pending first commit/PR | Commit/PR link, README screenshot, test output, CI URL. |

## Planned next milestone

Create a `Milestone 2: Configuration, finding model and MongoDB audit store` milestone only after Milestone 1’s remote evidence is recorded. Suggested initial issues:

1. Define typed audit configuration and its validation behavior.
2. Define stable finding/run models and serialized examples.
3. Implement optional, sanitized MongoDB audit persistence with offline mode.
4. Add disposable MongoDB integration tests, including connection-error and duplicate-run cases.

## Milestone 3 — First CLI vertical slice

| Issue | Status | Evidence / close condition |
| --- | --- | --- |
| Define one safe, declarative migration format | Complete locally | Manifest v1 is documented and parser tests cover valid/malformed input. |
| Deliver offline `check`, `rules`, and `version` commands | Complete locally | CLI tests cover text/JSON output and exit codes 0, 1, and 2. |
| Implement first deterministic rules | Complete locally | Unique-index, destructive-drop, and unsupported-operation fixtures are covered. |

## Labels

- `type: task`
- `type: bug`
- `type: documentation`
- `area: ci`
- `area: security`
- `area: mongodb`
- `status: blocked`

Do not include credentials, connection strings containing passwords, raw application documents, or production metadata in issues.
