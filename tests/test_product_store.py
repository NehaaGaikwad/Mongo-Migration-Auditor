from __future__ import annotations

from migration_auditor.product_store import ProductStore


def test_product_store_isolates_organization_audits(tmp_path) -> None:
    store = ProductStore(str(tmp_path / "product.sqlite3"))
    first = store.signup("first@example.com", "a secure password", "First", "First Org")
    second = store.signup("second@example.com", "another secure password", "Second", "Second Org")
    first_user, second_user = first["user"], second["user"]
    first_org = store.organizations_for(first_user["id"])[0]
    store.save_audit(first_org["id"], first_user["id"], {"findings": [], "policy_failed": False}, "0.1.0")

    assert len(store.audits_for(first_user)) == 1
    assert store.audits_for(second_user) == []
    assert store.owns_org(second_user["id"], first_org["id"]) is False


def test_login_never_returns_password_material(tmp_path) -> None:
    store = ProductStore(str(tmp_path / "product.sqlite3"))
    store.signup("person@example.com", "a secure password", "Person", "Workspace")
    session = store.login("person@example.com", "a secure password")

    assert session["token"]
    assert "password_hash" not in session["user"]
    assert store.user_for_token(session["token"])["email"] == "person@example.com"
