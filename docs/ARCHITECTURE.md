# Architecture

## Overview
React/TypeScript/Vite uses a local FastAPI service. SQLite stores metadata and review history; uploaded document binaries remain under local data storage. The API binds to loopback in the documented run command.

## Processing stages
1. Validate extension, MIME hint, byte limit, and file signature; create a random storage name.
2. Extract embedded PDF text page by page; image-backed pages with absent or sparse text try local Tesseract OCR through PyMuPDF. Images use optional local OCR. Missing optional tools are reported as limitations.
3. Normalize supported fields with a configurable document/board schema. Preserve original value, confidence type, page, evidence text, method, and separate reviewer correction metadata. Subject rows retain their own provenance.
4. Run deterministic consistency/quality rules and cross-document comparisons. An optional trained Isolation Forest emits an independent quality-outlier signal and feature deviations.
5. Retrieve policy passages from an explicitly ingested local Sentence Transformer vector index, filter by document type/board, and cite source/page/date. If model/index/evidence is absent, return insufficient evidence. No hosted LLM is required.
6. Use only the deterministic synthetic issuer provider behind explicit consent. Future real issuer and credential validation boundaries are typed interfaces; no real provider is implemented.
7. Record the human review separately. Retention cleanup is disabled by default and can be configured or explicitly invoked.

## Extension points
- `backend/schemas/document_schemas.json`: canonical document types, field aliases and board aliases.
- `backend/app/extraction.py`: page extraction, OCR fallback, evidence and subject rows.
- `backend/app/rag.py` and `scripts/ingest_policies.py`: source parsing, chunking, embedding, local vector index, filtering and citations.
- `backend/app/anomaly.py`: feature schema, training persistence, prediction explanations and controlled synthetic cases.
- `backend/app/issuer.py`: typed issuer/credential interfaces and the demo-only provider.

The JSON cosine-vector index is intended for the small demo corpus; a larger corpus should use an indexed vector database with access controls and lifecycle management.
