"""Opt-in disposable MongoDB integration coverage for the audit store."""

from __future__ import annotations

import os
from uuid import uuid4

import pytest
from pymongo import MongoClient

from migration_auditor.audit_store import MongoAuditStore
from migration_auditor.config import AuditStoreConfig
from tests.test_audit_store import sample_stored_run


@pytest.mark.integration
def test_mongo_store_round_trip_against_disposable_database() -> None:
    uri = os.getenv("MIGRATION_AUDITOR_TEST_MONGODB_URI")
    if not uri:
        pytest.skip("set MIGRATION_AUDITOR_TEST_MONGODB_URI to run MongoDB integration tests")

    database_name = f"migration_auditor_test_{uuid4().hex}"
    config = AuditStoreConfig(uri=uri, database=database_name, retention_days=1)
    store = MongoAuditStore.connect(config)
    try:
        store.ensure_indexes()
        stored_run = sample_stored_run()
        store.persist(stored_run)
        assert store.get_run(stored_run.run.run_id)["run_id"] == stored_run.run.run_id
    finally:
        store.close()
        cleanup_client = MongoClient(uri)
        try:
            # Test-only cleanup of a database name generated in this test.
            cleanup_client.drop_database(database_name)
        finally:
            cleanup_client.close()
