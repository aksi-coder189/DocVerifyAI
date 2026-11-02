# Implementation status

## Working in this demo
- Local upload, file validation, SQLite tracking, preview, report, reviewer notes/corrections, deletion, and synthetic issuer scenarios.
- Page-wise embedded PDF extraction with optional local scanned-page OCR; schema-driven field aliases, evidence/provenance, and conservative subject rows.
- Opt-in local semantic policy ingestion/retrieval with Sentence Transformer vectors, metadata filters, local citations, date/page metadata, and insufficient-evidence responses.
- Optional, separately trained Isolation Forest quality baseline with persistence, explanations, synthetic smoke evaluation, and no authenticity score.
- Cross-document name/roll comparisons, opt-in retention, and optional shared local token.

## Incomplete by design / future work
- Representative schemas/parsers/OCR QA across real academic boards and formats.
- Official policy sources and governance; verify licensing, issuer, dates, and document integrity during ingestion.
- Authorized issuer APIs and supported credential-format cryptographic trust validation.
- Real labeled anomaly data, deployment monitoring, validation, fairness/false-positive studies, and any accuracy claim.
- Multi-user auth, per-user access controls, tenant storage, secret management, deployment hardening, and a privacy/legal review.
- Indexed vector-store scaling beyond the JSON cosine store used for the small local demo.
