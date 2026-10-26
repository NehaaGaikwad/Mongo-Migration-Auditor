# Small local setup

Use this before adopting accounts, PostgreSQL, workers, or object storage. It is a single-process demonstration of the auditor and its operations dashboard.

1. Set `SIMPLE_ADMIN_MODE=true` in an uncommitted `.env` file or PowerShell session.
2. Start the API: `python -m uvicorn migration_auditor.api:app --reload --port 8000`.
3. Start the frontend from `frontend`: `python -m http.server 5173`.
4. Open `http://localhost:5173`, run one or more audits, then open `http://localhost:5173/admin.html`.

The dashboard shows only summaries from audits created since this API process started: status, policy result, finding count, file count, analyzer version, and time. Raw manifests are never retained. Restarting the API clears the dashboard.

For the optional local sign-in screen, the simple mode creates exactly one admin account on first startup: `admin@mongoauditor.local` / `Admin@12345!`. These are development-only credentials; set `SIMPLE_ADMIN_EMAIL` and `SIMPLE_ADMIN_PASSWORD` to change them before startup.

This mode has no accounts, data persistence, organization isolation, or administrative mutation. It must stay local. For multi-user or hosted use, leave `SIMPLE_ADMIN_MODE` disabled and use the product foundation documented in `product-mvp.md`.
