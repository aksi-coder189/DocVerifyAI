# API reference

All routes are prefixed with `/api`. The service is intended for a local single-user demo. Errors use FastAPI JSON `detail` responses.

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/health` | Local service status, OCR capabilities, and provider mode |
| GET | `/document-types` | Supported file/document families |
| GET | `/documents` | List active local cases |
| POST | `/documents` | Multipart upload using field `file` |
| GET | `/documents/{id}` | Case metadata |
| GET | `/documents/{id}/content` | Inline original document preview from local storage |
| GET | `/documents/{id}/report` | Fields/provenance, deterministic findings, cross-document checks, optional ML signal, RAG citations and review history |
| POST | `/documents/{id}/verify` | Simulated issuer check; JSON `{"demo_consent": true}` required |
| POST | `/documents/{id}/review` | Record `decision`, optional `note`, and field `corrections` |
| GET | `/documents/{id}/reviews` | Reviewer audit history |
| DELETE | `/documents/{id}` | Remove stored binary and case metadata |

Extraction fields include `{name, value, original_value, confidence, original_confidence, source_page, bounding_box, evidence_text, provenance, reviewer_correction?}`. Optional values may be null. Subject rows are returned under extraction separately. Cross-document checks preserve both source documents/pages and can be inconclusive at low confidence. `ml_anomaly` is a separately trained optional quality-outlier signal and is not a fraud prediction. Policy retrieval returns cited evidence, page and policy date when known, or `insufficient_evidence`. Issuer outcomes distinguish `matched`, `partial`, `mismatch`, `not_found`, `unavailable`, and `unsupported`; the only implemented provider is explicitly simulated. `DOCVERIFY_API_TOKEN` optionally protects private API routes with a shared bearer token; `/api/health` stays open.


Report `summary.statuses` separates `extraction`, `consistency_checks`, `review_signals`, `suspicious_findings`, and `issuer_verification`. The compatibility `summary.verdict` now uses safe values including `partial_extraction`, `needs_review`, `could_not_read`, and `automated_checks_passed`; a passed automated result is never issuer verification. The only implemented issuer provider is simulated, so it cannot produce an official verified status. Required fields and uncertain issuer extraction are reflected in `summary.missing_fields`.
