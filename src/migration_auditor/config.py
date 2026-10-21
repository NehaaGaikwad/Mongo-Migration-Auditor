"""Typed, strict configuration for the migration auditor."""

from __future__ import annotations

import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from migration_auditor.models import Severity


class ConfigurationError(ValueError):
    """Raised when configuration is missing, malformed, or unsafe to interpret."""


def _require_mapping(value: Any, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ConfigurationError(f"{name} must be a table/object")
    return value


def _optional_string(value: Any, name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ConfigurationError(f"{name} must be a string")
    value = value.strip()
    return value or None


def _string_list(value: Any, name: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(
        isinstance(item, str) and item.strip() for item in value
    ):
        raise ConfigurationError(f"{name} must be a list of non-empty strings")
    return tuple(item.strip() for item in value)


def _positive_int(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ConfigurationError(f"{name} must be a positive integer")
    return value


def _reject_unknown(mapping: Mapping[str, Any], allowed: set[str], name: str) -> None:
    unknown = set(mapping) - allowed
    if unknown:
        joined = ", ".join(sorted(unknown))
        raise ConfigurationError(f"{name} contains unsupported key(s): {joined}")


@dataclass(frozen=True)
class InputConfig:
    """Repository inputs used by the future discovery/baseline stages."""

    migration_paths: tuple[Path, ...]
    baseline_path: Path | None = None

    def __post_init__(self) -> None:
        if not self.migration_paths:
            raise ConfigurationError("inputs.migration_paths must not be empty")


@dataclass(frozen=True)
class RuleConfig:
    """Rule selection and severity overrides; rule IDs remain strings until the registry exists."""

    enabled: tuple[str, ...] = ()
    disabled: tuple[str, ...] = ()
    severity_overrides: Mapping[str, Severity] = field(default_factory=dict)

    def __post_init__(self) -> None:
        overlap = set(self.enabled) & set(self.disabled)
        if overlap:
            raise ConfigurationError(
                "rules.enabled and rules.disabled cannot contain the same rule: "
                + ", ".join(sorted(overlap))
            )


@dataclass(frozen=True)
class PolicyConfig:
    """Policy is separate from findings so severity can be configured without changing evidence."""

    fail_on: Severity = Severity.HIGH


@dataclass(frozen=True)
class AuditStoreConfig:
    """Connection and retention settings for optional audit metadata persistence."""

    uri: str | None = field(default=None, repr=False)
    database: str = "migration_auditor_metadata"
    runs_collection: str = "audit_runs"
    findings_collection: str = "audit_findings"
    server_selection_timeout_ms: int = 5_000
    connect_timeout_ms: int = 5_000
    retention_days: int = 90

    def __post_init__(self) -> None:
        if self.uri and not self.uri.startswith(("mongodb://", "mongodb+srv://")):
            raise ConfigurationError("audit_store.uri must start with mongodb:// or mongodb+srv://")
        for name, value in (
            ("audit_store.server_selection_timeout_ms", self.server_selection_timeout_ms),
            ("audit_store.connect_timeout_ms", self.connect_timeout_ms),
            ("audit_store.retention_days", self.retention_days),
        ):
            _positive_int(value, name)
        for name, value in (
            ("audit_store.database", self.database),
            ("audit_store.runs_collection", self.runs_collection),
            ("audit_store.findings_collection", self.findings_collection),
        ):
            if not value or not value.strip():
                raise ConfigurationError(f"{name} must not be empty")

    @property
    def enabled(self) -> bool:
        return self.uri is not None

    @property
    def retention_seconds(self) -> int:
        return self.retention_days * 24 * 60 * 60


@dataclass(frozen=True)
class AuditorConfig:
    """Complete normalized configuration contract for the auditor."""

    inputs: InputConfig
    rules: RuleConfig = field(default_factory=RuleConfig)
    policy: PolicyConfig = field(default_factory=PolicyConfig)
    audit_store: AuditStoreConfig = field(default_factory=AuditStoreConfig)

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any], *, base_path: Path = Path(".")) -> AuditorConfig:
        _reject_unknown(raw, {"inputs", "rules", "policy", "audit_store"}, "configuration")
        if "inputs" not in raw:
            raise ConfigurationError("configuration requires an [inputs] table")

        inputs_raw = _require_mapping(raw["inputs"], "inputs")
        _reject_unknown(inputs_raw, {"migration_paths", "baseline_path"}, "inputs")
        if "migration_paths" not in inputs_raw:
            raise ConfigurationError("inputs.migration_paths is required")
        migration_paths = tuple(
            (base_path / value).resolve()
            for value in _string_list(inputs_raw["migration_paths"], "inputs.migration_paths")
        )
        baseline_value = _optional_string(inputs_raw.get("baseline_path"), "inputs.baseline_path")
        inputs = InputConfig(
            migration_paths, (base_path / baseline_value).resolve() if baseline_value else None
        )

        rules_raw = _require_mapping(raw.get("rules", {}), "rules")
        _reject_unknown(rules_raw, {"enabled", "disabled", "severity_overrides"}, "rules")
        override_raw = _require_mapping(
            rules_raw.get("severity_overrides", {}), "rules.severity_overrides"
        )
        overrides: dict[str, Severity] = {}
        for rule_id, severity in override_raw.items():
            if not isinstance(rule_id, str) or not rule_id.strip():
                raise ConfigurationError("rules.severity_overrides keys must be non-empty strings")
            try:
                overrides[rule_id] = Severity(str(severity).lower())
            except ValueError as exc:
                raise ConfigurationError(
                    f"rules.severity_overrides.{rule_id} is not a valid severity"
                ) from exc
        rules = RuleConfig(
            enabled=_string_list(rules_raw.get("enabled", []), "rules.enabled"),
            disabled=_string_list(rules_raw.get("disabled", []), "rules.disabled"),
            severity_overrides=overrides,
        )

        policy_raw = _require_mapping(raw.get("policy", {}), "policy")
        _reject_unknown(policy_raw, {"fail_on"}, "policy")
        try:
            policy = PolicyConfig(Severity(str(policy_raw.get("fail_on", "high")).lower()))
        except ValueError as exc:
            raise ConfigurationError("policy.fail_on is not a valid severity") from exc

        store_raw = _require_mapping(raw.get("audit_store", {}), "audit_store")
        _reject_unknown(
            store_raw,
            {
                "uri",
                "database",
                "runs_collection",
                "findings_collection",
                "server_selection_timeout_ms",
                "connect_timeout_ms",
                "retention_days",
            },
            "audit_store",
        )
        audit_store = AuditStoreConfig(
            uri=_optional_string(store_raw.get("uri"), "audit_store.uri"),
            database=str(store_raw.get("database", "migration_auditor_metadata")),
            runs_collection=str(store_raw.get("runs_collection", "audit_runs")),
            findings_collection=str(store_raw.get("findings_collection", "audit_findings")),
            server_selection_timeout_ms=_positive_int(
                store_raw.get("server_selection_timeout_ms", 5_000),
                "audit_store.server_selection_timeout_ms",
            ),
            connect_timeout_ms=_positive_int(
                store_raw.get("connect_timeout_ms", 5_000), "audit_store.connect_timeout_ms"
            ),
            retention_days=_positive_int(
                store_raw.get("retention_days", 90), "audit_store.retention_days"
            ),
        )
        return cls(inputs=inputs, rules=rules, policy=policy, audit_store=audit_store)


def load_config(path: Path) -> AuditorConfig:
    """Load a TOML config file and resolve input paths relative to its parent directory."""

    try:
        with path.open("rb") as config_file:
            raw = tomllib.load(config_file)
    except FileNotFoundError as exc:
        raise ConfigurationError(f"configuration file not found: {path}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise ConfigurationError(f"invalid TOML in configuration file: {path}") from exc
    return AuditorConfig.from_mapping(raw, base_path=path.parent)
