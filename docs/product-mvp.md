# Product/admin MVP

This repository now provides a local, persistent product foundation around the static analyzer.

## Run locally

1. Copy `.env.example` to `.env` or set environment variables in the shell. Set a unique `BOOTSTRAP_ADMIN_EMAIL` and `BOOTSTRAP_ADMIN_PASSWORD` before starting the API; that account is created on first startup.
2. Run `python -m uvicorn migration_auditor.api:app --reload --port 8000` from the repository root.
3. In `frontend`, run `npm.cmd run build` or `python -m http.server 5173` for local static development.
4. Go to `/auth.html` to create a customer workspace, `/index.html` to submit and save an audit, and `/admin.html` after signing in with the bootstrap admin account.

## Implemented guarantees

- Passwords use PBKDF2-HMAC-SHA256 with a unique random salt; only derived hashes are stored.
- Browser sessions use random, time-limited, HttpOnly cookies; server persistence stores only SHA-256 token hashes.
- Product audit creation verifies active organization membership on the server.
- Customer history queries are membership-scoped; platform admin APIs require the server-side `admin` role.
- Raw uploads remain temporary. Persisted audit data contains the analyzer report, not source manifest artifacts.
- Administrative reads and authentication actions create append-only security events.

## Production work before release

The SQLite store is intentionally a local-MVP adapter. Production needs PostgreSQL migrations, managed identity with MFA, private object storage with retention, asynchronous jobs, rate limits, centralized telemetry, backups, and deployment secrets. Do not use the development database for an internet-facing deployment.
