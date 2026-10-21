from pathlib import Path

import pytest

from migration_auditor.config import AuditorConfig, ConfigurationError, load_config
from migration_auditor.models import Severity


def test_load_config_normalizes_paths_and_offline_store(tmp_path: Path) -> None:
    config_path = tmp_path / "migration-auditor.toml"
    config_path.write_text(
        """
[inputs]
migration_paths = ["migrations", "legacy"]
baseline_path = "baseline.json"

[rules]
enabled = ["MONGO-INDEX-001"]
disabled = []

[rules.severity_overrides]
"MONGO-INDEX-001" = "critical"

[policy]
fail_on = "high"
""",
        encoding="utf-8",
    )

    config = load_config(config_path)

    assert config.inputs.migration_paths == (tmp_path / "migrations", tmp_path / "legacy")
    assert config.inputs.baseline_path == tmp_path / "baseline.json"
    assert config.rules.severity_overrides == {"MONGO-INDEX-001": Severity.CRITICAL}
    assert config.audit_store.enabled is False


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        ({}, "requires an"),
        ({"inputs": {"migration_paths": []}}, "must not be empty"),
        ({"inputs": {"migration_paths": ["migrations"], "unexpected": True}}, "unsupported key"),
        (
            {
                "inputs": {"migration_paths": ["migrations"]},
                "rules": {"enabled": ["X"], "disabled": ["X"]},
            },
            "cannot contain the same rule",
        ),
    ],
)
def test_config_rejects_invalid_or_ambiguous_input(raw: dict[str, object], message: str) -> None:
    with pytest.raises(ConfigurationError, match=message):
        AuditorConfig.from_mapping(raw)


def test_config_rejects_unsafe_mongodb_uri_scheme() -> None:
    raw = {
        "inputs": {"migration_paths": ["migrations"]},
        "audit_store": {"uri": "https://example.invalid/database"},
    }

    with pytest.raises(ConfigurationError, match="must start with mongodb"):
        AuditorConfig.from_mapping(raw)
