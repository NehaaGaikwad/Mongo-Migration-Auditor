# Deploy the compact persistent setup

This setup uses one protected administrator account and a SQLite database on a Render persistent disk. It is appropriate for a small shared demonstration; it is not the multi-tenant production architecture described in `product-mvp.md`.

## Render API

1. Push this repository to GitHub and create a Render **Blueprint** from `render.yaml`.
2. In Render environment variables, set all of these before the first deploy:
   - `SIMPLE_ADMIN_EMAIL`: the private admin email you will share with your friend.
   - `SIMPLE_ADMIN_PASSWORD`: a unique password of at least 16 characters.
   - `ALLOWED_ORIGINS`: the exact frontend URL, for example `https://your-project.vercel.app`.
3. Keep the configured persistent disk. It stores `migration_auditor_product.sqlite3`; deleting the service or disk deletes the saved dashboard data.
4. Copy the public Render service URL and verify `/health` responds.

## Vercel frontend

1. Import the same GitHub repository into Vercel and select `frontend` as the root directory.
2. Add build variable `AUDITOR_API_URL` using the Render URL, without a trailing slash.
3. Deploy, then copy the Vercel URL into Render's `ALLOWED_ORIGINS` and redeploy the API.

## Access behavior

- The public site shows only the analyzer and a login button.
- `/admin.html` redirects to login when there is no valid session.
- The admin API and compact dashboard summary both verify the server-side admin role.
- Login fields are cleared on page load and after submitting; the application never pre-fills or displays the password.

Use HTTPS in both services. Browser privacy policies can restrict third-party cookies on some devices because the Vercel frontend and Render API are different sites. If that affects your users, host the frontend behind the same custom domain/reverse proxy as the API before sharing it broadly.
