# Troubleshooting and safe operation

| Symptom | Meaning and response |
| --- | --- |
| CLI exit `1` | A policy threshold was met. Inspect findings/remediation; this is not an analyzer crash. |
| CLI exit `2` | Configuration, parsing, persistence, or preflight evidence is incomplete. Correct the error before deployment. |
| `MONGO-ANALYSIS-001` | Input is unsupported/malformed, fail-closed, and cannot be suppressed. Rewrite it or review manually. |
| Preflight `incomplete`/`unknown` | Connectivity, permissions, timeout, scan limit, or syntax prevents a conclusion. It is not a clean result. |
| Audit persistence failure | Check the dedicated metadata database configuration and permissions; it must stay separate from the target database. |
| No audit history | `audit_store.uri` is absent, so offline behavior is expected. |
| MongoDB integration tests skipped | Provide disposable test-only URIs documented in the README. Never use production. |

Do not paste connection strings, sensitive report metadata, or application documents
into issues or CI logs. The analyzer never executes migrations; correct the input or
configuration and rerun safely.
