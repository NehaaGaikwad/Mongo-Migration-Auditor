"""Local product persistence for development; replace SQLite URL with PostgreSQL in deployment."""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import sqlite3
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4


def _now() -> str:
    return datetime.now(UTC).isoformat()


class ProductStore:
    def __init__(self, path: str | None = None) -> None:
        self.path = path or os.getenv("PRODUCT_DATABASE_PATH", "migration_auditor_product.sqlite3")
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def _initialize(self) -> None:
        with self._connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS users (id TEXT PRIMARY KEY, email TEXT UNIQUE NOT NULL,
                  display_name TEXT NOT NULL, password_hash TEXT NOT NULL, role TEXT NOT NULL,
                  status TEXT NOT NULL DEFAULT 'active', created_at TEXT NOT NULL, last_activity_at TEXT);
                CREATE TABLE IF NOT EXISTS organizations (id TEXT PRIMARY KEY, name TEXT NOT NULL,
                  slug TEXT UNIQUE NOT NULL, status TEXT NOT NULL DEFAULT 'active', created_by TEXT NOT NULL,
                  created_at TEXT NOT NULL, FOREIGN KEY(created_by) REFERENCES users(id));
                CREATE TABLE IF NOT EXISTS memberships (organization_id TEXT NOT NULL, user_id TEXT NOT NULL,
                  role TEXT NOT NULL, PRIMARY KEY(organization_id,user_id), FOREIGN KEY(organization_id) REFERENCES organizations(id),
                  FOREIGN KEY(user_id) REFERENCES users(id));
                CREATE TABLE IF NOT EXISTS sessions (token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL, expires_at TEXT NOT NULL,
                  FOREIGN KEY(user_id) REFERENCES users(id));
                CREATE TABLE IF NOT EXISTS audit_runs (id TEXT PRIMARY KEY, organization_id TEXT NOT NULL, requested_by TEXT NOT NULL,
                  status TEXT NOT NULL, policy_status TEXT NOT NULL, created_at TEXT NOT NULL, engine_version TEXT NOT NULL,
                  findings_count INTEGER NOT NULL, report_json TEXT NOT NULL, FOREIGN KEY(organization_id) REFERENCES organizations(id),
                  FOREIGN KEY(requested_by) REFERENCES users(id));
                CREATE TABLE IF NOT EXISTS security_events (id TEXT PRIMARY KEY, actor_id TEXT, action TEXT NOT NULL,
                  resource TEXT NOT NULL, outcome TEXT NOT NULL, created_at TEXT NOT NULL);
            """)
        email, password = os.getenv("BOOTSTRAP_ADMIN_EMAIL"), os.getenv("BOOTSTRAP_ADMIN_PASSWORD")
        if os.getenv("SIMPLE_ADMIN_MODE", "false").lower() == "true":
            email = email or os.getenv("SIMPLE_ADMIN_EMAIL", "admin@mongoauditor.local")
            password = password or os.getenv("SIMPLE_ADMIN_PASSWORD", "Admin@12345!")
        if email and password:
            with self._connect() as db:
                if not db.execute("SELECT 1 FROM users WHERE email=?", (email.lower(),)).fetchone():
                    now = _now()
                    db.execute("INSERT INTO users VALUES (?,?,?,?,?,?,?,?)", (str(uuid4()), email.lower(), "Admin", self._hash_password(password), "admin", "active", now, now))
        if os.getenv("SIMPLE_ADMIN_MODE", "false").lower() == "true":
            self._ensure_simple_workspace(email or "admin@mongoauditor.local")

    def _ensure_simple_workspace(self, email: str) -> None:
        """Create the one local owner/workspace used by the compact setup."""
        with self._connect() as db:
            user = db.execute("SELECT id FROM users WHERE email=?", (email.lower(),)).fetchone()
            if not user:
                return
            org = db.execute("SELECT id FROM organizations WHERE slug='local-admin'").fetchone()
            if not org:
                org_id, now = str(uuid4()), _now()
                db.execute("INSERT INTO organizations VALUES (?,?,?,?,?,?)", (org_id, "Local Admin", "local-admin", "active", user["id"], now))
            else:
                org_id = org["id"]
            db.execute("INSERT OR IGNORE INTO memberships VALUES (?,?,?)", (org_id, user["id"], "owner"))

    @staticmethod
    def _hash_password(password: str, salt: bytes | None = None) -> str:
        salt = salt or secrets.token_bytes(16)
        digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 310_000)
        return f"{salt.hex()}${digest.hex()}"

    @classmethod
    def _verify_password(cls, password: str, stored: str) -> bool:
        salt_hex, expected = stored.split("$", 1)
        return hmac.compare_digest(cls._hash_password(password, bytes.fromhex(salt_hex)).split("$", 1)[1], expected)

    def _user(self, row: sqlite3.Row) -> dict[str, Any]:
        return {key: row[key] for key in ("id", "email", "display_name", "role", "status", "created_at", "last_activity_at")}

    def signup(self, email: str, password: str, display_name: str, organization_name: str) -> dict[str, Any]:
        user_id, org_id, now = str(uuid4()), str(uuid4()), _now()
        slug = "".join(char if char.isalnum() else "-" for char in organization_name.lower()).strip("-") or "workspace"
        with self._connect() as db:
            if db.execute("SELECT 1 FROM users WHERE email = ?", (email.lower(),)).fetchone():
                raise ValueError("An account with this email already exists")
            base_slug, candidate, suffix = slug, slug, 2
            while db.execute("SELECT 1 FROM organizations WHERE slug = ?", (candidate,)).fetchone():
                candidate, suffix = f"{base_slug}-{suffix}", suffix + 1
            db.execute("INSERT INTO users VALUES (?,?,?,?,?,?,?,?)", (user_id, email.lower(), display_name, self._hash_password(password), "member", "active", now, now))
            db.execute("INSERT INTO organizations VALUES (?,?,?,?,?,?)", (org_id, organization_name, candidate, "active", user_id, now))
            db.execute("INSERT INTO memberships VALUES (?,?,?)", (org_id, user_id, "owner"))
        return self.create_session(user_id)

    def login(self, email: str, password: str) -> dict[str, Any]:
        with self._connect() as db:
            row = db.execute("SELECT * FROM users WHERE email = ?", (email.lower(),)).fetchone()
            if not row or row["status"] != "active" or not self._verify_password(password, row["password_hash"]):
                raise ValueError("Invalid email or password")
            db.execute("UPDATE users SET last_activity_at = ? WHERE id = ?", (_now(), row["id"]))
        return self.create_session(row["id"])

    def create_session(self, user_id: str) -> dict[str, Any]:
        token = secrets.token_urlsafe(32)
        expires = (datetime.now(UTC) + timedelta(days=7)).isoformat()
        with self._connect() as db:
            db.execute("INSERT INTO sessions VALUES (?,?,?)", (hashlib.sha256(token.encode()).hexdigest(), user_id, expires))
            row = db.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        return {"token": token, "user": self._user(row)}

    def user_for_token(self, token: str) -> dict[str, Any] | None:
        with self._connect() as db:
            row = db.execute("SELECT u.* FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.token_hash=? AND s.expires_at>?", (hashlib.sha256(token.encode()).hexdigest(), _now())).fetchone()
        return self._user(row) if row else None

    def end_session(self, token: str) -> None:
        with self._connect() as db:
            db.execute("DELETE FROM sessions WHERE token_hash = ?", (hashlib.sha256(token.encode()).hexdigest(),))

    def organizations_for(self, user_id: str) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute("SELECT o.*,m.role membership_role FROM organizations o JOIN memberships m ON m.organization_id=o.id WHERE m.user_id=?", (user_id,)).fetchall()
        return [dict(row) for row in rows]

    def owns_org(self, user_id: str, organization_id: str) -> bool:
        with self._connect() as db:
            return bool(db.execute("SELECT 1 FROM memberships WHERE user_id=? AND organization_id=?", (user_id, organization_id)).fetchone())

    def save_audit(self, organization_id: str, user_id: str, report: dict[str, Any], version: str) -> str:
        import json
        run_id = str(uuid4())
        status = "failed" if report.get("errors") else "completed"
        policy = "failed" if report.get("policy_failed") else "passed"
        with self._connect() as db:
            db.execute("INSERT INTO audit_runs VALUES (?,?,?,?,?,?,?,?,?)", (run_id, organization_id, user_id, status, policy, _now(), version, len(report.get("findings", [])), json.dumps(report)))
        return run_id

    def audits_for(self, user: dict[str, Any], all_tenants: bool = False) -> list[dict[str, Any]]:
        with self._connect() as db:
            query = "SELECT r.id,r.status,r.policy_status,r.created_at,r.engine_version,r.findings_count,o.name organization,u.email requested_by FROM audit_runs r JOIN organizations o ON o.id=r.organization_id JOIN users u ON u.id=r.requested_by"
            args: tuple[Any, ...] = ()
            if not all_tenants:
                query += " JOIN memberships m ON m.organization_id=r.organization_id WHERE m.user_id=?"
                args = (user["id"],)
            rows = db.execute(query + " ORDER BY r.created_at DESC", args).fetchall()
        return [dict(row) for row in rows]

    def admin_users(self) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute("SELECT id,email,display_name,role,status,created_at,last_activity_at FROM users ORDER BY created_at DESC").fetchall()
        return [dict(row) for row in rows]

    def admin_orgs(self) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute("SELECT o.id,o.name,o.slug,o.status,u.email owner,COUNT(m.user_id) members FROM organizations o JOIN users u ON u.id=o.created_by LEFT JOIN memberships m ON m.organization_id=o.id GROUP BY o.id ORDER BY o.created_at DESC").fetchall()
        return [dict(row) for row in rows]

    def security_events(self) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute("SELECT e.id,e.action,e.resource,e.outcome,e.created_at,u.email actor FROM security_events e LEFT JOIN users u ON u.id=e.actor_id ORDER BY e.created_at DESC LIMIT 100").fetchall()
        return [dict(row) for row in rows]

    def metrics(self) -> dict[str, int]:
        with self._connect() as db:
            return {
                "users": db.execute("SELECT COUNT(*) FROM users").fetchone()[0],
                "organizations": db.execute("SELECT COUNT(*) FROM organizations").fetchone()[0],
                "audits": db.execute("SELECT COUNT(*) FROM audit_runs").fetchone()[0],
                "execution_failures": db.execute("SELECT COUNT(*) FROM audit_runs WHERE status = 'failed'").fetchone()[0],
                "policy_failures": db.execute("SELECT COUNT(*) FROM audit_runs WHERE policy_status = 'failed'").fetchone()[0],
            }

    def simple_context(self) -> tuple[dict[str, Any], dict[str, Any]] | None:
        email = os.getenv("SIMPLE_ADMIN_EMAIL", os.getenv("BOOTSTRAP_ADMIN_EMAIL", "admin@mongoauditor.local"))
        with self._connect() as db:
            user = db.execute("SELECT * FROM users WHERE email=?", (email.lower(),)).fetchone()
            org = db.execute("SELECT * FROM organizations WHERE slug='local-admin'").fetchone()
        return (self._user(user), dict(org)) if user and org else None

    def simple_runs(self) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute("SELECT id,status,policy_status,created_at,engine_version,findings_count FROM audit_runs ORDER BY created_at DESC LIMIT 50").fetchall()
        return [dict(row) for row in rows]

    def event(self, actor: str | None, action: str, resource: str, outcome: str) -> None:
        with self._connect() as db:
            db.execute("INSERT INTO security_events VALUES (?,?,?,?,?,?)", (str(uuid4()), actor, action, resource, outcome, _now()))
