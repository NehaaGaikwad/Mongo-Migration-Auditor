"""Pytest configuration for isolated Windows-safe temporary directories."""

from __future__ import annotations

from pathlib import Path
from tempfile import mkdtemp

import pytest


def pytest_configure(config: pytest.Config) -> None:
    """Avoid stale pytest roots created by a different Windows security principal.

    Tests use pytest's ``tmp_path`` fixture. Giving each test session a newly
    generated base directory prevents pytest from attempting to delete either a
    repository-local shared directory or an inaccessible stale ``pytest-of-*``
    directory in the system temp location. An explicit command-line
    ``--basetemp`` remains authoritative.
    """

    if config.option.basetemp is None:
        config.option.basetemp = Path(mkdtemp(prefix="mongo-migration-auditor-pytest-"))
