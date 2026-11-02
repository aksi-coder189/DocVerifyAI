# Security and privacy

## Current controls
- API validates extension, MIME hint, file signature, and configured maximum size; generated filenames prevent path traversal. Content paths are resolved and checked before serving.
- Files and case metadata are local by default. Extraction and embedding inference run locally; RAG model downloads occur only when the explicit ingestion script is run. Document text is not sent to a paid or hosted inference API.
- Optional shared bearer-token middleware can protect private API endpoints in a single local workspace. Keep API bound to `127.0.0.1`; do not expose the shared frontend token or rely on it for multiple users.
- Retention is disabled by default. A configured startup policy or explicit cleanup command deletes metadata, stored files, and review rows. Per-case deletion removes stored content and records.
- Reviewer corrections retain the original extracted value/confidence/provenance and are recorded separately with a timestamp.

## Not production-ready
There is no user identity, role-based access control, per-user partitioning, secrets manager, hardened session mechanism, abuse/rate limit control, key rotation, or multi-user audit boundary. The optional shared token is included in the UI bundle when enabled and is not safe as a public deployment credential. Add proper authentication/authorization, tenant isolation, operational security, consent/retention policy, and legal review before serving other users or making high-impact decisions.

Synthetic issuer records are test fixtures, not official results. Bundled policy text is synthetic and is not authoritative. Issuer unavailability, no match, extraction failure, or anomaly flags must never be interpreted as fraud or ineligibility.
