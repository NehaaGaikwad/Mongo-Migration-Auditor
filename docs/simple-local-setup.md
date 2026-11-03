# Small local setup

Use this before adopting accounts, PostgreSQL, workers, or object storage. It is a single-process demonstration of the auditor and its operations dashboard.

1. Set `SIMPLE_ADMIN_MODE=true`, `SIMPLE_ADMIN_EMAIL`, and a long unique
   `SIMPLE_ADMIN_PASSWORD` in an uncommitted `.env` file or PowerShell session.
2. Start the API: `python -m uvicorn migration_auditor.api:app --reload --port 8000`.
3. Start the frontend from `frontend`: `python -m http.server 5173`.
4. Open `http://localhost:5173`, run one or more audits, then open `http://localhost:5173/admin.html`.

The dashboard shows only summaries from audits created since this API process started: status, policy result, finding count, file count, analyzer version, and time. Raw manifests are never retained. Restarting the API clears the dashboard.

For the optional local sign-in screen, simple mode creates exactly one admin account
only when both explicit credentials are supplied. It has no fallback/default
password. Never reuse a shared or production credential.

This mode has no accounts, data persistence, organization isolation, or administrative mutation. It must stay local. For multi-user or hosted use, leave `SIMPLE_ADMIN_MODE` disabled and use the product foundation documented in `product-mvp.md`.
